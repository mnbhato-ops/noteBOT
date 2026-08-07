import sys
import os
import json
from datetime import datetime
import time
import requests
import base64

# 環境変数の読み込み (Gemini APIキーとnoteのセッション情報のみで完結)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTE_SESSION_STATE = os.environ.get("NOTE_SESSION_STATE")

# noteのトレンドタグを取得する際のエラー回避用
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

# --- 1. noteからトレンドタグを取得 ---
def get_note_trending_tag():
    print("1/5 noteからトレンドを取得中...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto("https://note.com/topic", timeout=15000)
            page.wait_for_timeout(4000)
            
            tags = page.locator("a[href*='/hashtag/']").all_inner_texts()
            browser.close()
            
            if tags:
                clean_tag = tags[0].replace("#", "").strip()
                print(f" -> 取得成功: #{clean_tag}", flush=True)
                return clean_tag
        except Exception as e:
            print(f" -> トレンド取得時の注意: {e}", flush=True)
            browser.close()
            
        print(" -> トレンド取得失敗のためデフォルトタグを使用します", flush=True)
        return "AI活用"

# --- 2. Gemini APIで記事を生成 ---
def generate_article(keyword):
    print("2/5 Gemini APIで記事を生成中...", flush=True)
    
    current_hour = datetime.now().hour
    if current_hour < 12:
        time_style = "【朝の投稿スタイル】通勤通学時間にサクッと読める実用的な内容"
    else:
        time_style = "【夜の投稿スタイル】一日の終わりにじっくり読める深掘りした内容"

    prompt = f"""
    現在noteで注目されているトレンドテーマ「#{keyword}」について記事を作成してください。
    {time_style}
    
    【ルール】
    ・1行目：キャッチーな記事タイトルのみ（「タイトル：」などの接頭辞は不要）
    ・2行目以降：本文（1500文字程度）
    ・文体は親しみやすく丁寧な「〜です・〜ます」調
    ・末尾に関連するハッシュタグ（#{keyword} など）を3つ含める
    ・HTMLタグは含めない
    """
    
    # 最新の gemini-1.5-flash モデルを使用
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    for attempt in range(3):
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            break
        elif response.status_code == 429:
            print(f" -> リクエスト制限(429)のため2分間(120秒)待機して再試行します... ({attempt + 1}/3)", flush=True)
            time.sleep(120)
        else:
            print(f"APIエラー詳細: {response.text}", flush=True)
            raise Exception(f"Gemini APIリクエスト失敗 Status: {response.status_code}")

    res_json = response.json()
    content = res_json['candidates'][0]['content']['parts'][0]['text']
    
    lines = content.strip().split("\n")
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return title, body

# --- 3. Gemini APIで画像生成プロンプトを作成 ---
def generate_image_prompt(title, body, keyword):
    print("3/5 Geminiで画像プロンプトを作成中...", flush=True)
    
    prompt_for_gemini = f"""
    以下のnote記事のタイトルと本文から、この記事のカバー画像（アイキャッチ画像）として最適な、高品質な画像を生成するための詳細なプロンプトを作成してください。

    【記事タイトル】
    {title}

    【記事本文（抜粋）】
    {body[:500]}...

    【出力ルール】
    ・画像生成AI（Imagen 3）に入力できる、具体的で詳細な**英語のプロンプト**のみを出力してください。
    ・画像のスタイルは、プロフェッショナルで、現代的、そして記事の内容（#{keyword}）に合ったものにしてください。
    ・テキストや文字を画像内に含めないように指示してください("without any text or letters")。
    ・出力はプロンプトの英語テキストのみにしてください。
    """

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt_for_gemini}]
        }]
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        res_json = response.json()
        image_prompt = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
        print(f"   -> 生成された画像プロンプト: {image_prompt[:50]}...", flush=True)
        return image_prompt
    else:
        print(f" -> 画像プロンプト生成エラー: {response.text}", flush=True)
        return f"A modern and professional illustration representing the concept of '{keyword}' for a blog post cover, without any text."

# --- 4. Gemini API (Imagen 3) で画像を生成 ---
def generate_image(image_prompt, filename="cover_image.jpeg"):
    print("4/5 Gemini (Imagen 3) で画像を生成中...", flush=True)
    
    # Imagen 3のREST APIエンドポイント
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    # aspectRatioに16:9を指定し、noteのカバー画像に最適なサイズにする
    payload = {
        "instances": [
            {
                "prompt": image_prompt
            }
        ],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "16:9" 
        }
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        res_json = response.json()
        
        # 予測結果からbase64エンコードされた画像データを取得
        if "predictions" in res_json and len(res_json["predictions"]) > 0:
            prediction = res_json["predictions"][0]
            b64_data = prediction.get("bytesBase64Encoded")
            
            if not b64_data:
                raise Exception("画像データがレスポンスに含まれていません")
                
            image_data = base64.b64decode(b64_data)
            with open(filename, "wb") as f:
                f.write(image_data)
                
            print(f" -> 画像生成・保存成功: {filename}", flush=True)
            return filename
        else:
            raise Exception("予期しないAPIレスポンス形式です")
            
    except Exception as e:
        print(f" -> 画像生成失敗: {e}", flush=True)
        if 'response' in locals() and response.text:
            print(f" -> エラー詳細: {response.text}", flush=True)
        return None

# --- 5. noteへ投稿 (カバー画像アップロード機能追加) ---
def post_to_note(title, body, image_path):
    print("5/5 noteへの自動投稿を実行中...", flush=True)
    
    if not image_path:
        print(" -> 注意: カバー画像がないため、画像なしで投稿します。", flush=True)

    state_data = json.loads(NOTE_SESSION_STATE)
    state_file = "temp_state.json"
    with open(state_file, "w") as f:
        json.dump(state_data, f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=state_file,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            print(" -> 直接記事執筆画面へ移動中...", flush=True)
            page.goto("https://note.com/notes/new", wait_until="networkidle")
            
            title_selector = "textarea[placeholder*='タイトル']"
            page.wait_for_selector(title_selector, timeout=30000)
            page.fill(title_selector, title)
            page.wait_for_timeout(1000)
            
            body_selector = "div[data-placeholder*='本文'], div[contenteditable='true']"
            page.fill(body_selector, body)
            page.wait_for_timeout(2000)

            # --- カバー画像のアップロード処理 ---
            if image_path and os.path.exists(image_path):
                print(" -> カバー画像をアップロード中...", flush=True)
                try:
                    cover_image_selector = "button:has-text('カバー画像を設定')"
                    page.wait_for_selector(cover_image_selector, timeout=15000)
                    
                    with page.expect_file_chooser() as fc_info:
                        page.click(cover_image_selector)
                    
                    file_chooser = fc_info.value
                    file_chooser.set_files(image_path)
                    
                    print("   -> アップロード完了を待機中...", flush=True)
                    page.wait_for_timeout(10000) 
                    print("   -> アップロード処理完了", flush=True)

                except Exception as e:
                    print(f" -> カバー画像のアップロードに失敗しました (スキップします): {e}", flush=True)
            
            # --- 公開処理 ---
            print(" -> 1/2 「公開設定」ボタンをクリックします...", flush=True)
            publish_config_button = "button:has-text('公開設定')"
            page.wait_for_selector(publish_config_button, timeout=15000)
            page.click(publish_config_button)
            page.wait_for_timeout(5000) 
            
            print(" -> 2/2 最終「投稿する」ボタンをクリックします...", flush=True)
            final_post_button = "button:has-text('投稿する'), button:has-text('記事を公開')"
            page.wait_for_selector(final_post_button, timeout=15000)
            page.wait_for_timeout(2000)
            page.click(final_post_button)
            
            print("   -> 投稿処理の完了を待機中...", flush=True)
            page.wait_for_timeout(10000)
            print(" -> 投稿処理がすべて完了しました！", flush=True)

        except Exception as e:
            print(f" -> 投稿処理中にエラーが発生しました: {e}", flush=True)
        finally:
            browser.close()
            if os.path.exists(state_file):
                os.remove(state_file)

# --- メイン処理 ---
if __name__ == "__main__":
    print("--- プログラムを開始します ---", flush=True)
    
    if not all([GEMINI_API_KEY, NOTE_SESSION_STATE]):
        print("エラー: 環境変数が正しく設定されていません。(GEMINI_API_KEY, NOTE_SESSION_STATE が必要)", flush=True)
        sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright
        print("Playwrightライブラリの読み込み完了", flush=True)
    except Exception as e:
        print(f"Playwrightライブラリの読み込みでエラーが発生しました: {e}", flush=True)
        print(" -> 'pip install playwright' と 'playwright install' を実行してください。", flush=True)
        sys.exit(1)

    # 1. noteからトレンドタグを取得
    keyword = get_note_trending_tag()
    
    # 2. Geminiで記事を生成
    title, body = generate_article(keyword)
    print(f"\n生成タイトル: {title}\n", flush=True)

    # 3. Geminiで画像プロンプトを作成
    image_prompt = generate_image_prompt(title, body, keyword)
    
    # 4. Gemini (Imagen 3) で画像を生成
    cover_image_filename = f"cover_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpeg"
    image_path = generate_image(image_prompt, filename=cover_image_filename)

    # 5. noteへ投稿
    post_to_note(title, body, image_path)

    # 6. 一時画像ファイルの削除
    if image_path and os.path.exists(image_path):
        os.remove(image_path)
        print(f" -> 一時画像ファイルを削除しました: {image_path}", flush=True)

    print("--- すべての処理が完了しました ---", flush=True)
