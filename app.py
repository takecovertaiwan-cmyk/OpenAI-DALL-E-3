# ====================================================================
# [A] WesmartAI 證據報告 Web App (final_definitive_flow)
# 作者: Gemini & User
# --------------------------------------------------------------------
# [A1] 核心架構 (最終定案):
# 1. 確立最終使用者流程：多次生成預覽 -> 一次性結束並下載所有原圖 -> 可選地生成PDF報告。
# 2. 前端恢復 Seed 與尺寸輸入，後端 /generate 同步接收。
# 3. /finalize_session 作為核心，處理整個任務的證據封裝，並回傳所有圖片連結。
# 4. JSON 證據檔案僅存於後端，不提供給使用者。
# --------------------------------------------------------------------
# [A2] 系統特性
# - 整合 FLUX API (Black-Forest-Labs)
# - 全程以 SHA-256 驗證雜湊鏈結
# - 可離線驗證 JSON 與 PDF 對應一致性
# ====================================================================

# === B1. 套件匯入 ===
import requests, json, hashlib, uuid, datetime, random, time, os, io, base64
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from PIL import Image
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import qrcode

# === B2. 讀取環境變數 (已修改為 OPENAI_API_KEY) ===
[cite_start]API_key = os.getenv("OPENAI_API_KEY") [cite: 1]

# === B3. Flask App 初始化 ===
app = Flask(__name__)
static_folder = 'static'
if not os.path.exists(static_folder): os.makedirs(static_folder)
app.config['UPLOAD_FOLDER'] = static_folder

# === C1. 工具函式 ===
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()

# === C2. PDF 報告類別 ===
class WesmartPDFReport(FPDF):
    # C2-1. 初始化 (含字型下載)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not os.path.exists("NotoSansTC.otf"):
            print("正在下載中文字型...")
            try:
                r = requests.get("https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf")
                r.raise_for_status()
                with open("NotoSansTC.otf", "wb") as f: f.write(r.content)
                print("字型下載完成。")
            except Exception as e: print(f"字型下載失敗: {e}")
        self.add_font("NotoSansTC", "", "NotoSansTC.otf")
        self.set_auto_page_break(auto=True, margin=25); self.alias_nb_pages()
        self.logo_path = "LOGO.jpg" if os.path.exists("LOGO.jpg") else None
    
    # C2-2. 頁首
    def header(self):
        if self.logo_path:
            with self.local_context(fill_opacity=0.08, stroke_opacity=0.08):
                img_w=120; center_x=(self.w-img_w)/2; center_y=(self.h-img_w)/2; self.image(self.logo_path, x=center_x, y=center_y, w=img_w)
        if self.page_no() > 1: self.set_font("NotoSansTC", "", 9); self.set_text_color(128); self.cell(0, 10, "WesmartAI 生成式 AI 證據報告", new_x=XPos.LMARGIN, new_y=YPos.TOP, align='L'); self.cell(0, 10, "WesmartAI Inc.", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    
    # C2-3. 頁尾
    def footer(self): self.set_y(-15); self.set_font("NotoSansTC", "", 8); self.set_text_color(128); self.cell(0, 10, f'第 {self.page_no()}/{{nb}} 頁', align='C')
    
    # C2-4. 章節標題
    def chapter_title(self, title): self.set_font("NotoSansTC", "", 16); self.set_text_color(0); self.cell(0, 12, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L'); self.ln(6)
    
    # C2-5. 章節內文
    def chapter_body(self, content): self.set_font("NotoSansTC", "", 10); self.set_text_color(50); self.multi_cell(0, 7, content, align='L'); self.ln()
    
    # C2-6. 封面頁
    def create_cover(self, meta):
        self.add_page();
        if self.logo_path: self.image(self.logo_path, x=(self.w-60)/2, y=25, w=60)
        self.set_y(100); self.set_font("NotoSansTC", "", 28); self.cell(0, 20, "WesmartAI 證據報告", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C'); self.ln(20)
        self.set_font("NotoSansTC", "", 12)
        data = [("出證申請人:", meta.get('applicant', 'N/A')), ("申請事項:", "WesmartAI 生成式 AI 證據報告"), ("申請出證時間:", meta.get('issued_at', 'N/A')), ("出證編號 (報告ID):", meta.get('report_id', 'N/A')), ("出證單位:", meta.get('issuer', 'N/A'))]
        for row in data: self.cell(20); self.cell(45, 10, row[0], align='L'); self.multi_cell(0, 10, row[1], new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    
    # C2-7. 任務細節頁 (已更新為六重 File Hash)
    def create_generation_details_page(self, proof_data):
        self.add_page();
        self.chapter_title("一、各版本生成快照")
        
        for snapshot in proof_data['event_proof']['snapshots']:
            self.set_font("NotoSansTC", "", 12); self.set_text_color(0);
            self.cell(0, 10, f"版本索引: {snapshot['version_index']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L'); self.ln(2)
            
            # 顯示 Step Hash
            self.set_font("NotoSansTC", "", 10); self.set_text_color(0)
            self.cell(40, 8, "  Step Hash:", align='L');
            self.set_font("Courier", "", 9); self.set_text_color(80)
            self.multi_cell(0, 8, snapshot['hashes']['step_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(2)

            # 顯示基本資料
            details = [
                ("時間戳記 (UTC)", snapshot['timestamp_utc']),
                ("輸入指令 (Prompt)", snapshot['prompt']),
                ("隨機種子 (Seed)", str(snapshot['seed'])),
                ("尺寸 (寬x高)", f"{snapshot['width']} x {snapshot['height']}")
            ]
            for key, value in details:
                self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, f"  - {key}:", align='L');
                self.set_font("NotoSansTC", "", 9); self.set_text_color(80)
                self.multi_cell(0, 7, str(value), align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            # 顯示六重雜湊 (File Hash 取代 Image Hash)
            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 時間戳雜湊:", align='L');
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['timestamp_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 檔案雜湊 (File):", align='L'); # <--- 關鍵升級
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['file_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT) # <--- 關鍵升級

            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 指令雜湊:", align='L');
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['prompt_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 種子雜湊:", align='L');
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['seed_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 寬度雜湊:", align='L');
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['width_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            self.set_font("NotoSansTC", "", 10); self.set_text_color(0); self.cell(60, 7, "  - 高度雜湊:", align='L');
            self.set_font("Courier", "", 8); self.set_text_color(120)
            self.multi_cell(0, 7, snapshot['hashes']['height_hash'], align='L', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            # 顯示圖像
            self.ln(5)
            try:
                img_bytes = base64.b64decode(snapshot['content_base64'])
                img_file_obj = io.BytesIO(img_bytes)
                self.image(img_file_obj, x=(self.w-80)/2, w=80, type='PNG')
            except Exception as e: print(f"在PDF中顯示圖片失敗: {e}")
            self.ln(15)
    
    # C2-8. 結論驗證頁 (已更新為六重 File Hash 說明)
    def create_conclusion_page(self, proof_data):
        self.add_page(); self.chapter_title("三、報告驗證")
        
        # 需求 #2: 更新說明文字
        self.chapter_body(
            "本報告之真實性與完整性，係依據每一生成頁面所記錄之六重雜湊（時間戳雜湊、檔案雜湊、提示詞雜湊、種子雜湊、寬度雜湊與高度雜湊）逐步累積計算所得。\n" # <--- 關鍵升級
            "每頁六重雜湊經系統自動組合為單一 Step Hash，而所有 Step Hash 再依序整合為最終之 Final Event Hash。\n"
            "Final Event Hash 為整份創作過程的唯一驗證憑證，代表該份報告內所有頁面與內容在生成當下的完整性。\n"
            "任何後續對圖像、提示詞或時間資料的竄改，皆將導致對應之 Step Hash 與 Final Event Hash 不一致，可藉此進行真偽比對與法律層面的舉證。"
        )
        
        self.ln(10); self.set_font("NotoSansTC", "", 12); self.set_text_color(0)
        self.cell(0, 10, "最終事件雜湊值 (Final Event Hash):", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Courier", "B", 11)
        self.multi_cell(0, 8, proof_data['event_proof']['final_event_hash'], border=1, align='C', padding=5)
        qr_data = proof_data['verification']['verify_url']
        qr = qrcode.make(qr_data); qr_path = os.path.join(app.config['UPLOAD_FOLDER'], f"qr_{proof_data['report_id'][:10]}.png"); qr.save(qr_path)
        self.ln(10); self.set_font("NotoSansTC", "", 10); self.cell(0, 10, "掃描 QR Code 前往驗證頁面", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.image(qr_path, w=50, x=(self.w-50)/2)

# === D1. 全域狀態 ===
session_previews = []
latest_proof_data = None

# === D2. 首頁 (重置狀態) ===
@app.route('/')
def index():
    global session_previews, latest_proof_data
    session_previews = []
    latest_proof_data = None
    return render_template('index.html', api_key_set=bool(API_key))

# === E1. /generate: 步驟1: 生成預覽圖 ===
@app.route('/generate', methods=['POST'])
def generate():
    if not API_key: 
        return jsonify({"error": "後端尚未設定 BFL_API_KEY 環境變數"}), 500
    
    data = request.json
    prompt = data.get('prompt')
    if not prompt: 
        return jsonify({"error": "Prompt 為必填項"}), 400

    try:
        # E1-0. 獲取前端參數 (尺寸已固定)
        seed_input = data.get('seed')
        width = 2752
        height = 1536
        seed_value = int(seed_input) if seed_input and seed_input.isdigit() else random.randint(1, 10**9)
        
        # E1-1. 提交生成任務到 FLUX API
        endpoint = "https://api.bfl.ai/v1/flux-pro-1.1-ultra"
        headers = {"accept": "application/json", "x-key": API_key, "Content-Type": "application/json"}
        payload = {"prompt": prompt, "width": width, "height": height, "seed": seed_value}
        
        initial_res = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        initial_res.raise_for_status()
        response_data = initial_res.json()
        polling_url = response_data.get('polling_url')

        if not polling_url:
            return jsonify({"error": f"API 未返回 polling_url: {response_data}"}), 500

        # E1-2. 輪詢 (Polling) 以獲取結果
        polling_headers = {"accept": "application/json", "x-key": API_key}
        image_url = None
        start_time = time.time()
        
        while time.time() - start_time < 120: # 設定 120 秒超時
            time.sleep(1) # 每秒輪詢一次
            poll_res = requests.get(polling_url, headers=polling_headers, timeout=60)
            poll_res.raise_for_status()
            result_data = poll_res.json()
            status = result_data.get('status')

            if status == "Ready":
                image_url = result_data.get('result', {}).get('sample')
                break
            elif status in ["Error", "Failed"]:
                error_message = result_data.get('error', 'Unknown error during generation')
                return jsonify({"error": f"生成失敗: {error_message}"}), 500
        
        if not image_url:
            return jsonify({"error": "生成超時，無法在時限內取得結果"}), 500
        
        # E1-3. 從返回的 URL 下載圖片
        img_bytes = requests.get(image_url, timeout=60).content
        
        # E1-4. 從返回的 URL 下載圖片
        
        # E1-5. 儲存預覽圖檔案
        filename = f"preview_v{len(session_previews) + 1}_{int(time.time())}.png"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        Image.open(io.BytesIO(img_bytes)).save(filepath)

        # E1-6. 產生六重雜湊與 Step Hash (最終版：使用 File Hash)
        timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        img_base64_str = base64.b64encode(img_bytes).decode('utf-8') # 仍需 Base64 供 PDF 顯示

        # 1. 六重雜湊 (File Hash 取代 Image Hash)
        timestamp_hash = sha256_bytes(timestamp_utc.encode('utf-8'))
        prompt_hash = sha256_bytes(prompt.encode('utf-8'))
        seed_hash = sha256_bytes(str(seed_value).encode('utf-8'))
        width_hash = sha256_bytes(str(width).encode('utf-8'))
        height_hash = sha256_bytes(str(height).encode('utf-8'))
        file_hash = sha256_bytes(img_bytes) # <--- 關鍵升級：對原始二進位檔案雜湊

        # 2. 打包生成 Step Hash
        step_hash_input = json.dumps({
            "timestamp_hash": timestamp_hash,
            "prompt_hash": prompt_hash,
            "seed_hash": seed_hash,
            "width_hash": width_hash,
            "height_hash": height_hash,
            "file_hash": file_hash  # <--- 關鍵升級
        }, sort_keys=True).encode('utf-8')
        step_hash = sha256_bytes(step_hash_input)

        # E1-7. 暫存所有紀錄 (並包含錯誤修正)
        session_previews.append({
            "prompt": prompt, "seed": seed_value, "model": "flux-pro-1.1-ultra",
            "width": width, "height": height, "filepath": filepath,
            "timestamp_utc": timestamp_utc,
            "content_base64": img_base64_str, # 供 PDF 顯示
            "hashes": {
                "timestamp_hash": timestamp_hash,
                "prompt_hash": prompt_hash,
                "seed_hash": seed_hash,
                "width_hash": width_hash,
                "height_hash": height_hash,
                "file_hash": file_hash, # <--- 關鍵升級
                "step_hash": step_hash
            }
        })
        
        return jsonify({
            "success": True, 
            "preview_url": url_for('static_preview', filename=filename),
            "version": len(session_previews)
        })

    # === [E1-EXCEPT] 錯誤處理 (修復 SyntaxError) ===
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"網路請求失敗: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"生成過程中發生未知錯誤: {str(e)}"}), 500

# === E2. /finalize_session: 步驟2: 結束任務，生成所有證據正本 ===
@app.route('/finalize_session', methods=['POST'])
def finalize_session():
    global latest_proof_data, session_previews
    applicant_name = request.json.get('applicant_name')
    if not applicant_name: return jsonify({"error": "出證申請人名稱為必填項"}), 400
    if not session_previews: return jsonify({"error": "沒有任何預覽圖像可供結束任務"}), 400

    try:
        snapshots = []
        image_urls = []
        
       # E2-1. 迭代所有預覽圖快照 (已包含雜湊)
        for i, preview in enumerate(session_previews):
            snapshots.append({
                "version_index": i + 1,
                "timestamp_utc": preview['timestamp_utc'],
                "prompt": preview['prompt'],
                "seed": preview['seed'],
                "model": preview['model'],
                "hashes": preview['hashes'], # 包含四重雜湊 + Step Hash
                "content_base64": preview['content_base64']
            })
            image_urls.append(url_for('static_download', filename=os.path.basename(preview['filepath'])))

        # E2-2. 產生報告 ID 與 Final Event Hash (需求 #4)
        report_id = str(uuid.uuid4())
        issued_at_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # 需求 #4: 每個頁面step hash 最後打包成為 final event_hash
        all_step_hashes = [s['hashes']['step_hash'] for s in snapshots]
        final_hash_input = json.dumps(all_step_hashes, sort_keys=True).encode('utf-8')
        final_event_hash = sha256_bytes(final_hash_input)

        # 移除舊的 trace_token (已被 step_hash 取代)
        # temp_proof_for_hashing ... (此段已不需要)

        # E2-3. 組合並儲存 JSON 證據正本
        proof_data = {
            "report_id": report_id, "issuer": "WesmartAI Inc.", "applicant": applicant_name, "issued_at": issued_at_iso,
            "event_proof": {
                "final_event_hash": final_event_hash,
                "snapshots": snapshots # Snapshots 現在已包含所有 hashes
            },
            "verification": {"verify_url": f"https://wesmart.ai/verify?hash={final_event_hash}"}
        }

        json_filename = f"proof_event_{report_id}.json"
        json_filepath = os.path.join(app.config['UPLOAD_FOLDER'], json_filename)
        with open(json_filepath, 'w', encoding='utf-8') as f:
            json.dump(proof_data, f, ensure_ascii=False, indent=2)
        print(f"證據正本已儲存至: {json_filename}")

        latest_proof_data = proof_data

        return jsonify({"success": True, "image_urls": image_urls})

    except Exception as e:
        print(f"結束任務失敗: {e}")
        return jsonify({"error": f"結束任務失敗: {str(e)}"}), 500

# === E3. /create_report: 步驟3: 產生 PDF 報告 ===
@app.route('/create_report', methods=['POST'])
def create_report():
    if not latest_proof_data: return jsonify({"error": "請先結束任務並生成證據"}), 400
    
    try:
        # E3-1. 呼叫 PDF 類別產生報告
        report_id = latest_proof_data['report_id']
        pdf = WesmartPDFReport()
        pdf.create_cover(latest_proof_data)
        pdf.create_generation_details_page(latest_proof_data)
        pdf.create_conclusion_page(latest_proof_data)
        
        # E3-2. 儲存 PDF 檔案
        report_filename = f"WesmartAI_Report_{report_id}.pdf"
        report_filepath = os.path.join(app.config['UPLOAD_FOLDER'], report_filename)
        pdf.output(report_filepath)

        return jsonify({"success": True, "report_url": url_for('static_download', filename=report_filename)})
    except Exception as e:
        print(f"報告生成失敗: {e}")
        return jsonify({"error": f"報告生成失敗: {str(e)}"}), 500

# === F. 靜態檔案路由 ===
# F1. 預覽圖路由
@app.route('/static/preview/<path:filename>')
def static_preview(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# F2. 下載路由
@app.route('/static/download/<path:filename>')
def static_download(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)

# === G. 啟動服務 ===
if __name__ == '__main__':
    app.run(debug=True)






