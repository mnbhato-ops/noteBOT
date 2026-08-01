import sys
import os
from datetime import datetime
import time
import requests

print("--- プログラムを開始します ---", flush=True)

try:
    from playwright.sync_api import sync_playwright
    print("ライブラリの読み込み完了", flush=True)
except Exception as e:
    print(f"ライブラリの読み込みでエラーが発生しました: {e}", flush=True)
    sys.exit(1)

# --- 設定項目（GitHub ActionsのSecretsから取得） ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTE_EMAIL = os.environ.get("NOTE_EMAIL")
NOTE_PASSWORD = os.environ.get("NOTE_PASSWORD")

if not all([GEMINI_API_KEY, NOTE_EMAIL, NOTE_PASSWORD]):
    print("エラー: 環境変数が正しく設定されていません。", flush=True)
    sys.exit(1)

# 1. noteからトレンドタグを取得
def get_note_trending_tag():
    print("1/3 noteからトレンドを取得中...", flush=True)
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

# 2. 直接HTTP通信でGemini APIを呼び出して記事作成
def generate_article(keyword):
    print("2/3 Gemini APIで記事を生成中...", flush=True)
    
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

# 3. noteへ投稿
def post_to_note(title, body):
    print("3/3 noteへの自動投稿を実行中...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        print(" -> noteログイン画面へ移動中...", flush=True)
        page.goto("https://note.com/login")
        page.wait_for_timeout(3000)
        
        email_selector = "input[name='login'], input[type='email'], input[id='email']"
        page.wait_for_selector(email_selector, timeout=15000)
        page.fill(email_selector, NOTE_EMAIL)
        
        password_selector = "input[name='password'], input[type='password']"
        page.fill(password_selector, NOTE_PASSWORD)
        
        login_button = "button:has-text('ログイン'), button[type='submit'], input[type='submit']"
        page.click(login_button)
        print(" -> ログインボタンをクリックしました", flush=True)
        page.wait_for_timeout(5000)
        
        print(" -> 記事執筆画面へ移動中...", flush=True)
        page.goto("https://note.com/notes/new")
        
        title_selector = "textarea[placeholder*='タイトル'], textarea[placeholder*='記事タイトル']"
        page.wait_for_selector(title_selector, timeout=15000)
        
        page.fill(title_selector, title)
        page.wait_for_timeout(1000)
        
        body_selector = "div[data-placeholder*='本文'], div[contenteditable='true']"
        page.fill(body_selector, body)
        page.wait_for_timeout(3000)
        
        print(" -> 1/2 「公開設定」ボタンをクリックします...", flush=True)
        publish_config_button = "button:has-text('公開設定'), button:has-text('公開に進む')"
        page.wait_for_selector(publish_config_button, timeout=10000)
        page.click(publish_config_button)
        page.wait_for_timeout(4000)
        
        print(" -> 2/2 最終「投稿する」ボタンをクリックします...", flush=True)
        final_post_button = "button:has-text('投稿する'), button:has-text('記事を公開'), button:has-text('公開する')"
        page.wait_for_selector(final_post_button, timeout=10000)
        page.click(final_post_button)
        
        page.wait_for_timeout(5000)
        browser.close()
        print(" -> 投稿処理がすべて完了しました！", flush=True)

if __name__ == "__main__":
    keyword = get_note_trending_tag()
    title, body = generate_article(keyword)
    print(f"\n生成タイトル: {title}\n", flush=True)
    post_to_note(title, body)
