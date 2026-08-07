import sys
import os
import json
from datetime import datetime
import time
import requests
import base64

print("--- プログラムを開始します ---", flush=True)

try:
    from playwright.sync_api import sync_playwright
    print("ライブラリの読み込み完了", flush=True)
except Exception as e:
    print(f"ライブラリの読み込みでエラーが発生しました: {e}", flush=True)
    sys.exit(1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTE_SESSION_STATE = os.environ.get("NOTE_SESSION_STATE")

if not all([GEMINI_API_KEY, NOTE_SESSION_STATE]):
    print("エラー: 環境変数が正しく設定されていません。", flush=True)
    sys.exit(1)

# 1. noteからトレンドタグを取得（元のコード）
def get_note_trending_tag():
    print("1/4 noteからトレンドを取得中...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto("https://note.com/topic", timeout=10000)
            page.wait_for_timeout(3000)
            
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

# 2. 直接HTTP通信でGemini APIを呼び出して記事作成（元のコード）
def generate_article(keyword):
    print("2/4 Gemini APIで記事を生成中...", flush=True)
    
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
    """
    
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

    if response.status_code != 200:
        raise Exception(f"Gemini APIリクエスト失敗 Status: {response.status_code}")

    res_json = response.json()
    content = res_json['candidates'][0]['content']['parts'][0]['text']
    
    lines = content.strip().split("\n")
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    return title, body

# 3. Gemini (Imagen 3) でカバー画像を生成する処理
def generate_cover_image(title, keyword):
    print("3/4 Gemini (Imagen 3) でカバー画像を生成中...", flush=True)
    
    # 画像用プロンプト作成
    prompt_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt_req = {
        "contents": [{
            "parts": [{"text": f"記事タイトル「{title}」（テーマ: #{keyword}）に合うカバー画像の詳細な英語プロンプトを1文で出力してください。画像内に文字を含めない指示をつけてください。"}]
        }]
    }
    
    try:
        res = requests.post(prompt_url, json=prompt_req, timeout=30).json()
        img_prompt = res['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception:
        img_prompt = f"A modern professional illustration representing {keyword}, no text"

    # Imagen 3 で画像生成
    imagen_url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={GEMINI_API_KEY}"
    imagen_req = {
        "instances": [{"prompt": img_prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "16:9"}
    }
    
    try:
        res = requests.post(imagen_url, json=imagen_req, timeout=90).json()
        b64 = res["predictions"][0]["bytesBase64Encoded"]
        image_path = "cover.jpeg"
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(b64))
        print(" -> カバー画像の生成完了", flush=True)
        return image_path
    except Exception as e:
        print(f" -> 画像生成に失敗したため画像なしで続行します: {e}", flush=True)
        return None

# 4. noteへ投稿（元の成功コードに画像設定のみを挿入）
def post_to_note(title, body, image_path=None):
    print("4/4 noteへの自動投稿を実行中...", flush=True)
    
    # Secretsから読み込んだセッション状態を一時ファイルとして復元
    state_data = json.loads(NOTE_SESSION_STATE)
    with open("temp_state.json", "w") as f:
        json.dump(state_data, f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 保存したログイン状態を読み込んでコンテキストを作成
        context = browser.new_context(
            storage_state="temp_state.json",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print(" -> 直接記事執筆画面へ移動中...", flush=True)
        page.goto("https://note.com/notes/new", wait_until="networkidle")
        
        title_selector = "textarea[placeholder*='タイトル'], textarea[placeholder*='記事タイトル'], textarea"
        page.wait_for_selector(title_selector, timeout=30000)
        
        page.fill(title_selector, title)
        page.wait_for_timeout(1000)
        
        body_selector = "div[data-placeholder*='本文'], div[contenteditable='true']"
        page.fill(body_selector, body)
        page.wait_for_timeout(3000)
        
        # --- 追加: 画像が存在する場合のみカバー画像を設定 ---
        if image_path and os.path.exists(image_path):
            print(" -> カバー画像をアップロード中...", flush=True)
            try:
                cover_btn = "button:has-text('カバー画像を設定')"
                page.wait_for_selector(cover_btn, timeout=10000)
                with page.expect_file_chooser() as fc_info:
                    page.click(cover_btn)
                file_chooser = fc_info.value
                file_chooser.set_files(image_path)
                page.wait_for_timeout(5000)
            except Exception as e:
                print(f" -> 画像設定失敗（スキップして続行します）: {e}", flush=True)

        # --- 以下、元の成功コードと完全に同一の投稿フロー ---
        print(" -> 1/2 「公開設定」ボタンをクリックします...", flush=True)
        publish_config_button = "button:has-text('公開設定'), button:has-text('公開に進む')"
        page.wait_for_selector(publish_config_button, timeout=15000)
        page.click(publish_config_button)
        page.wait_for_timeout(4000)
        
        print(" -> 2/2 最終「投稿する」ボタンをクリックします...", flush=True)
        final_post_button = "button:has-text('投稿する'), button:has-text('記事を公開'), button:has-text('公開する')"
        page.wait_for_selector(final_post_button, timeout=15000)
        page.click(final_post_button)
        
        page.wait_for_timeout(5000)
        browser.close()
        
        # 一時ファイルの削除
        if os.path.exists("temp_state.json"):
            os.remove("temp_state.json")
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
            
        print(" -> 投稿処理がすべて完了しました！", flush=True)

if __name__ == "__main__":
    keyword = get_note_trending_tag()
    title, body = generate_article(keyword)
    print(f"\n生成タイトル: {title}\n", flush=True)
    
    # Geminiで画像を生成
    image_path = generate_cover_image(title, keyword)
    
    # 元の投稿処理へ引き渡し
    post_to_note(title, body, image_path)
