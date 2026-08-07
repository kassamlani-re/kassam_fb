from fastapi import FastAPI, Query, Response
import os

app = FastAPI()

# يقرأ المفتاح ديناميكياً من إعدادات Render لكي لا تضطر لتغييره في الكود مستقبلاً
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "KFMsr3Amir19012026R")

@app.get("/webhook")
def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        # إرجاع الـ challenge كـ int مباشرة يحل مشكلة الرفض في ميتا
        if challenge and challenge.isdigit():
            return Response(content=challenge, media_type="text/plain")
        return Response(content=challenge, media_type="text/plain")
        
    return Response(content="Verification failed", status_code=403)

# 2. مسار استقبال الأحداث (POST): يرسل له فيسبوك الرسائل والتعليقات الحية فور حدوثها
@app.post("/webhook")
async def handle_facebook_events(request: Request):
    payload = await request.json()
    print("📩 حدث جديد قادم من فيسبوك:", payload)
    
    # هنا ستضع منطق معالجة البيانات والرد عبر Gemini لاحقاً
    return {"status": "success"}

