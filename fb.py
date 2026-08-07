from fastapi import FastAPI, Request, Query, Response

app = FastAPI()

# هذا هو "مفتاح التحقق" الذي ستخترعه بنفسك وتكتبه في لوحة ميتا
VERIFY_TOKEN = "BFMSR_SaaS_Secret_2026"

# 1. مسار التحقق (GET): تطلبه ميتا مرة واحدة فقط عند الضغط على "تحقق وحفظ"
@app.get("/webhook")
def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ تم التحقق من الـ Webhook بنجاح وارتباطه مع ميتا!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="فشل التحقق، المفتاح غير متطابق", status_code=403)

# 2. مسار استقبال الأحداث (POST): يرسل له فيسبوك الرسائل والتعليقات الحية فور حدوثها
@app.post("/webhook")
async def handle_facebook_events(request: Request):
    payload = await request.json()
    print("📩 حدث جديد قادم من فيسبوك:", payload)
    
    # هنا ستضع منطق معالجة البيانات والرد عبر Gemini لاحقاً
    return {"status": "success"}

