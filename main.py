from flask import Flask, request, abort
from google import genai
import os
import pandas as pd
import requests
from io import StringIO
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, PostbackEvent

app = Flask(__name__)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

configuration = Configuration(access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET"))

system_prompt = """你是一個專業的藥物交互作用查詢助手，使用繁體中文回答。
只回答關於藥物與藥物、藥物與保健品是否相衝的問題。
如果用戶問其他問題，請禮貌地說明你只能回答藥物相關問題。
回答時請提醒用戶最終仍需諮詢醫師或藥師。"""

user_data = {}
user_history = {}
drug_df = None
supplement_df = None
health_df=None
def load_drug_data():
    global drug_df, supplement_df,health_df
    try:
        sheet_id = "16Vka0eNWBA9qM_zIij-rACfNVHenXguvDJcU1jlg6tE"
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        r = requests.get(url)
        r.encoding = 'utf-8'
        drug_df = pd.read_csv(StringIO(r.text))
        print("藥物資料庫載入成功")
    except Exception as e:
        print(f"藥物資料庫載入失敗：{e}")
    try:
        sheet_id2 = "1Rs0ijna-rC4QQYBFNyb6MTwme45j9IK-5CYCgAok4d4"
        url2 = f"https://docs.google.com/spreadsheets/d/{sheet_id2}/export?format=csv"
        r2 = requests.get(url2)
        r2.encoding = 'utf-8'
        supplement_df = pd.read_csv(StringIO(r2.text))
        print("保健品資料庫載入成功")
    except Exception as e:
        print(f"保健品資料庫載入失敗：{e}")
        
    try:
        health_df = pd.read_csv('19_2.csv', encoding='utf-8')
        print("健康食品資料庫載入成功")
    except Exception as e:
        print(f"健康食品資料庫載入失敗：{e}")
def search_drug(name):
    if supplement_df is not None:
        result = supplement_df[supplement_df['中文品名'].str.contains(name, na=False)]
        if not result.empty:
            row = result.iloc[0]
            return {
                "中文品名": row.get("中文品名", ""),
                "英文品名": row.get("英文品名", ""),
                "主成分": row.get("主成分", ""),
                "藥品類別": row.get("類別", "")
            }
    if drug_df is None:
        return None
    result = drug_df[drug_df['中文品名'].str.contains(name, na=False)]
    if result.empty:
        result = drug_df[drug_df['英文品名'].str.contains(name, na=False, case=False)]
    if not result.empty:
        row = result.iloc[0]
        return {
            "中文品名": row.get("中文品名", ""),
            "英文品名": row.get("英文品名", ""),
            "主成分": row.get("主成分略述", ""),
            "藥品類別": row.get("藥品類別", "")
        }
    return None

def make_query_form():
    flex = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "藥物相互作用查詢", "weight": "bold", "size": "lg"},
                {"type": "text", "text": "藥物／保健品 1：（商品名或成分均可）", "size": "sm"},
                {"type": "button", "action": {"type": "postback", "label": "輸入藥物1", "data": "action=input&field=drug1"}, "style": "secondary"},
                {"type": "text", "text": "藥物／保健品 2：（商品名或成分均可）", "size": "sm"},
                {"type": "button", "action": {"type": "postback", "label": "輸入藥物2", "data": "action=input&field=drug2"}, "style": "secondary"},
                {"type": "button", "action": {"type": "postback", "label": "查詢", "data": "action=query"}, "style": "primary", "color": "#00B900"}
            ]
        }
    }
    return FlexMessage(alt_text="藥物查詢表單", contents=FlexContainer.from_dict(flex))

def ask_gemini(user_id, user_msg):
    if user_id not in user_history:
        user_history[user_id] = []
    user_history[user_id].append({"role": "user", "parts": [user_msg]})
    full_prompt = system_prompt + "\n\n"
    for h in user_history[user_id]:
        if h["role"] == "user":
            full_prompt += f"用戶：{h['parts'][0]}\n"
        else:
            full_prompt += f"助手：{h['parts'][0]}\n"
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=full_prompt
    )
    reply = response.text
    user_history[user_id].append({"role": "model", "parts": [reply]})
    if len(user_history[user_id]) > 20:
        user_history[user_id] = user_history[user_id][-20:]
    return reply
@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text

    if user_msg in ["查詢", "開始", "你好", "hi", "Hi"]:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[make_query_form()])
            )
        return

    if user_id in user_data and "waiting_for" in user_data[user_id]:
        field = user_data[user_id]["waiting_for"]
        user_data[user_id][field] = user_msg
        del user_data[user_id]["waiting_for"]
        reply = f"已記錄：{user_msg}，請繼續填寫或點查詢。"
    else:
        reply = ask_gemini(user_id, user_msg)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
        )

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data

    if user_id not in user_data:
        user_data[user_id] = {}

    if "field=drug1" in data:
        user_data[user_id]["waiting_for"] = "drug1"
        reply = "請輸入第一個藥物或保健品名稱："
    elif "field=drug2" in data:
        user_data[user_id]["waiting_for"] = "drug2"
        reply = "請輸入第二個藥物或保健品名稱："
    elif "action=query" in data:
        drug1 = user_data[user_id].get("drug1", "")
        drug2 = user_data[user_id].get("drug2", "")
        if not drug1 or not drug2:
            reply = "請先輸入兩個藥物名稱再查詢。"
        else:
            info1 = search_drug(drug1)
            info2 = search_drug(drug2)
            q = "請分析以下兩個藥物是否有相互作用或禁忌：\n"
            if info1:
                q += f"藥物1：{drug1}，主成分：{info1['主成分']}\n"
            else:
                q += f"藥物1：{drug1}\n"
            if info2:
                q += f"藥物2：{drug2}，主成分：{info2['主成分']}\n"
            else:
                q += f"藥物2：{drug2}\n"
            reply = ask_gemini(user_id, q)
            user_data[user_id] = {}
    else:
        reply = "請重新開始，傳送「查詢」。"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply)])
        )

if __name__ == "__main__":
    load_drug_data()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
