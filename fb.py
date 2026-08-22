# ==========================================
# SECTION 1: IMPORTS & CONFIGURATIONS
# ==========================================

import os
import requests
from fastapi import FastAPI, Request, Response, HTTPException
from dotenv import load_dotenv
from supabase import create_client, Client as SupabaseClient
from cryptography.fernet import Fernet
from google import genai

# 1. شحن متغيرات البيئة من ملف .env
load_dotenv()

# 2. تهيئة تطبيق FastAPI الأساسي
app = FastAPI(title="SaaS Facebook Bot Endpoint")

# 3. إعداد وتهيئة عميل قاعدة البيانات Supabase
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")
supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)

# 4. إعداد وتهيئة عميل Google GenAI الحديث والموحد
# المكتبة الجديدة تقوم بقراءة GEMINI_API_KEY تلقائياً من الذاكرة دون الحاجة لأمر configure
ai_client = genai.Client()

# 5. تهيئة نظام التشفير (Fernet/AES) لحماية الـ Tokens الحساسة
ENCRYPTION_KEY: bytes = os.getenv("ENCRYPTION_KEY", "").encode()
cipher_suite = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None

# 6. رمز التحقق الخاص بالـ Webhook (مطابق تماماً لاسم المتغير في Render)
VERIFY_TOKEN: str = os.getenv("VERIFY_TOKEN", "default_secret_token")

# ==========================================
# SECTION 2: HELPER FUNCTIONS (الدوال المساعدة)
# ==========================================

# ------------------------------------------------------------------
# القسم (أ): دالة التحقق من وجود الصفحة في قاعدة البيانات وجلب بياناتها
# ------------------------------------------------------------------

def check_page_subscription(page_id: str) -> dict:
    """
    التسلسل الوظيفي 1:
    تأخذ معرف الصفحة القادم من حدث فيسبوك، وتفحص جدول (fb_pages) في Supabase.
    إذا كانت الصفحة مسجلة، تُرجع بياناتها (التوكن المشفر ومعرف العميل)، وإذا لم تكن مسجلة تُرجع None.
    """
    try:
        # الاستعلام عن سطر الصفحة لجلب التوكن المشفر ومعرف المستخدم (التاجر)
        response = supabase.table("fb_pages") \
            .select("page_access_token, user_id") \
            .eq("page_id", str(page_id)) \
            .execute()
        
        # إذا كانت المصفوفة تحتوي على بيانات، فهذا يعني أن التاجر مشترك لدينا
        if response.data:
            return response.data[0]  # إرجاع القاموس (Dictionary) الخاص ببيانات الصفحة
            
        print(f"⚠️ تنبيه وظيفي: استقبلنا حدثاً من الصفحة {page_id} ولكنها غير مسجلة في منصة SaaS الخاصة بنا.")
        return None
        
    except Exception as e:
        print(f"🚨 خطأ هندسي أثناء الاتصال بـ Supabase للتحقق من الصفحة: {e}")
        return None
# ------------------------------------------------------------------
# القسم (ب): دالة فك تشفير توكن الفيسبوك بعد جلبها من قاعدة البيانات
# ------------------------------------------------------------------

def get_decrypted_token(encrypted_token: str) -> str:
    """
    التسلسل الوظيفي 2:
    تستقبل النص المشفر الذي جلبناه في الخطوة السابقة من جدول fb_pages،
    وتقوم بفك تشفيره رياضياً في المعالج باستخدام مفتاح Fernet السري،
    لتُرجع التوكن الأصلي الصالح لإرسال الطلبات إلى فيسبوك.
    """
    # فحص أمني: التأكد من أن مفتاح التشفير متوفر في الذاكرة
    if not cipher_suite:
        raise ValueError("خطأ معماري: مفتاح التشفير (ENCRYPTION_KEY) غير موجود في ريندر!")
        
    try:
        # 1. تحويل النص المشفر القادم من Supabase إلى بايتات (Bytes) لأن مكتبة التشفير لا تقبل النصوص
        encrypted_bytes: bytes = encrypted_token.encode()
        
        # 2. عملية فك التشفير الرياضية باستخدام خوارزمية AES-128 عبر Fernet
        decrypted_bytes: bytes = cipher_suite.decrypt(encrypted_bytes)
        
        # 3. تحويل البايتات الناتجة مجدداً إلى نص مقروء (String) باستخدام ترميز UTF-8 العالمي
        decrypted_token: str = decrypted_bytes.decode('utf-8')
        
        return decrypted_token
        
    except Exception as e:
        print(f"🚨 خطأ فادح أثناء فك التشفير: قد يكون مفتاح التشفير خاطئاً أو النص تالفاً! التفاصيل: {e}")
        raise e
# ------------------------------------------------------------------
# القسم (ت): دالة جلب الملف التجاري ومعلومات العمل الخاصة بالتاجر
# ------------------------------------------------------------------

def get_business_profile(user_id: str) -> dict:
    """
    التسلسل الوظيفي 3:
    تأخذ الـ user_id الخاص بالتاجر (والذي حصلنا عليه من دالة الفحص في القسم أ)،
    وتستعلم من جدول (business_profiles) في Supabase عن معلومات عمله.
    تُرجع قاموساً بالبيانات، أو قاموساً فارغاً في حال لم يقم التاجر بملء بياناته بعد.
    """
    try:
        response = supabase.table("business_profiles") \
            .select("business_name, business_type, phone_number, address, working_hours") \
            .eq("user_id", str(user_id)) \
            .execute()
            
        if response.data:
            return response.data[0]  # إرجاع السطر الأول والوحيد الخاص بهذا التاجر
            
        return {}  # إرجاع قاموس فارغ إذا لم تكن هناك بيانات مصاغة
        
    except Exception as e:
        print(f"⚠️ تنبيه معماري: تعذر جلب الملف التجاري للمستخدم {user_id}. التفاصيل: {e}")
        return {}
# ------------------------------------------------------------------
# القسم (ث): دالة إرسال مؤشر الكتابة (Typing Indicator) إلى مسنجر فيسبوك
# ------------------------------------------------------------------

def send_typing_indicator(fb_user_id: str, page_access_token: str, action: str = "typing_on") -> bool:
    # الرابط في سطر واحد مفصول بعلامة الزائد لمنع الاختصار التلقائي
    fb_url = "https://" + "graph." + ":/facebook.com" + "/v19.0/me/messages"
    
    # تمرير توكن الوصول كمعامل استعلام آمن ومستقل
    params = {"access_token": page_access_token}
    
    payload = {
        "recipient": {"id": str(fb_user_id)},
        "sender_action": action
    }
    
    try:
        # إرسال الطلب لفيسبوك
        response = requests.post(fb_url, params=params, json=payload, timeout=5)
        
        if response.status_code == 200:
            return True
            
        print(f"⚠️ تنبيه فيسبوك: كود الخطأ {response.status_code}, التفاصيل: {response.text}")
        return False
        
    except Exception as e:
        print(f"🚨 خطأ هندسي أثناء إرسال مؤشر الكتابة: {e}")
        return False

# ------------------------------------------------------------------
# القسم (ج): دالة جلب آخر الرسائل المتبادلة (الذاكرة والسياق) لـ Gemini
# ------------------------------------------------------------------

def get_chat_context(page_id: str, fb_user_id: str, limit: int = 10) -> list:
    """
    التسلسل الوظيفي 5:
    تذهب إلى جدول (chat_history) في Supabase وتجلب آخر 10 رسائل (أو تعليقات)
    متبادلة بين هذه الصفحة بالذات وهذا الزبون بالذات.
    تُرجع مصفوفة مرتبة زمنياً من الأقدم إلى الأحدث ليفهمها الذكاء الاصطناعي كقصة مستمرة.
    """
    try:
        # الاستعلام عن الرسائل السابقة وفرزها تنازلياً لجلب الأحدث أولاً بناءً على الـ limit
        response = supabase.table("chat_history") \
            .select("sender, message_text") \
            .eq("page_id", str(page_id)) \
            .eq("fb_user_id", str(fb_user_id)) \
            .order("created_at", descending=True) \
            .limit(limit) \
            .execute()
        
        # الفحص الهندسي: إذا كانت هناك محادثات سابقة، نعكس ترتيبها
        if response.data:
            # نعكسها لكي تقرأها بايثون وGemini بالترتيب الطبيعي: (رسالة المستخدم ثم رد البوت)
            return list(reversed(response.data))
            
        # إذا كانت هذه أول رسالة يرسلها الزبون للبوت تماماً، نُرجع مصفوفة فارغة
        return []
        
    except Exception as e:
        print(f"⚠️ تنبيه معماري: تعذر جلب سياق المحادثة من Supabase. التفاصيل: {e}")
        return []
# ------------------------------------------------------------------
# القسم (ح): دالة إرسال السياق والرسالة باستخدام مكتبة google-genai الحديثة
# ------------------------------------------------------------------

def ask_gemini_bot(system_instruction: str, business_info: dict, chat_history: list, new_message: str, temperature: float = 0.3) -> str:
    """
    التسلسل الوظيفي 6:
    تستخدم عميل جوجل الموحد والحديث لعام 2026 لتوليد ردود فصيحة وبشرية
    عبر الموديل الرائد gemini-2.5-flash وبأعلى سرعة ممكنة للـ Webhooks.
    """
    try:
        # 1. صياغة وتجهيز نص معلومات المتجر/العيادة الحية
        biz_text = ""
        if business_info:
            biz_text = (
                f"\n[معلومات عمل التاجر الحالية والدقيقة]:\n"
                f"- اسم النشاط: {business_info.get('business_name')}\n"
                f"- نوع ومجال العمل: {business_info.get('business_type')}\n"
                f"- رقم الهاتف: {business_info.get('phone_number', 'غير محدد')}\n"
                f"- العنوان الجغرافي: {business_info.get('address', 'غير محدد')}\n"
                f"- ساعات وأوقات العمل: {business_info.get('working_hours', 'غير محدد')}\n"
            )
            
        # 2. بناء وتجميع الـ 10 رسائل السابقة بشكل نصي متسلسل لحفظ الذاكرة
        context_text = ""
        if chat_history:
            context_text = "\n[سياق المحادثة السابقة بينكما]:\n"
            for msg in chat_history:
                sender_label = "الزبون" if msg['sender'] == 'user' else "أنت (البوت)"
                context_text += f"{sender_label}: {msg['message_text']}\n"
                
        # 3. دمج الهيكل بأكمله لصناعة الـ Prompt النهائي والمحكم
        final_prompt = (
            f"[تعليماتك الشخصية وشخصيتك الأساسية]:\n{system_instruction}\n"
            f"{biz_text}\n"
            f"{context_text}\n"
            f"[الرسالة الحالية والجديدة القادمة من الزبون الآن]:\n{new_message}\n\n"
            f"التعليمات النهائية للصياغة: صغ رداً لابقاً، ذكياً، ومباشراً يتماشى تماماً مع طبيعة عملك والمعلومات المتاحة، "
            f"واجعل الرد مختصراً ومناسباً لشات فيسبوك دون زيادة أو تأليف معلومات غير موجودة. اكتب الرد مباشرة:"
        )
        
        # 4. الاستدعاء باستخدام الهيكلية والمكتبة الموحدة الجديدة من جوجل لعام 2026
        # نمرر درجة الحرارة (temperature) مباشرة لتثبيت دقة الحقائق ومنع الهلوسة
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=final_prompt,
            config={'temperature': temperature}
        )
        
        return response.text.strip()
        
    except Exception as e:
        print(f"🚨 خطأ هندسي أثناء التخاطب مع عميل Google GenAI الحديث: {e}")
        return "أهلاً بك، نعتذر عن هذا التأخر البسيط. يمكنك ترك استفسارك وسنقوم بالرد عليك في أقرب وقت ممكن!"

# ------------------------------------------------------------------
# القسم (خ): دالة حفظ الرسائل والردود الجديدة في جدول سجل المحادثات
# ------------------------------------------------------------------

def save_chat_to_history(page_id: str, fb_user_id: str, sender_type: str, message_text: str) -> bool:
    """
    التسلسل الوظيفي 7:
    تأخذ معرف الصفحة، ومعرف الزبون، ونوع المرسل (إما 'user' للزبون أو 'bot' للبوت)، ونص الرسالة.
    وتقوم بعمل إدخال (Insert) فوري داخل جدول chat_history في Supabase لتحديث ذاكرة البوت حياً.
    تُرجع True إذا تم الحفظ بنجاح.
    """
    # فحص القيمة المدخلة للتأكد من مطابقتها للقيود الصارمة (Constraints) التي وضعناها في SQL
    if sender_type not in ['user', 'bot']:
        print(f"⚠️ خطأ منطقي: نوع المرسل '{sender_type}' غير مدعوم في قاعدة البيانات.")
        return False

    try:
        # إرسال طلب الإدخال إلى Supabase
        supabase.table("chat_history").insert({
            "page_id": str(page_id),
            "fb_user_id": str(fb_user_id),
            "sender": sender_type,
            "message_text": message_text
        }).execute()
        
        return True
        
    except Exception as e:
        print(f"🚨 خطأ هندسي أثناء تدوين الرسالة في جدول chat_history: {e}")
        return False
# ------------------------------------------------------------------
# القسم (ذ): دالة إرسال الرد الآلي كتعليق أسفل منشور الفيسبوك
# ------------------------------------------------------------------

def send_facebook_comment_reply(comment_id: str, page_access_token: str, reply_text: str) -> bool:
    """
    التسلسل الوظيفي 9:
    تأخذ معرف التعليق، التوكن المفكوك، ونص الرد الصادر من Gemini،
    وترسل طلب HTTP POST إلى واجهة Meta Graph API بشكل مباشر ومتصل.
    """
    # كتابة الرابط متصلاً بشكل طبيعي وبدون أي تفكيك هندسي
    fb_url = f"https://facebook.com{comment_id}/comments?access_token={page_access_token}"
    
    # الهيكل (Payload) المطلوب من مطوري ميتا للرد على التعليقات
    payload = {
        "message": reply_text
    }
    
    try:
        # إرسال نبضة الرد إلى السيرفرات الخارجية مع تحديد مهلة انتظار (Timeout)
        response = requests.post(fb_url, json=payload, timeout=5)
        
        # كود الاستجابة 200 يعني أن الرد تم نشره بنجاح أسفل منشورك
        if response.status_code == 200:
            return True
            
        print(f"⚠️ تنبيه إرسال تعليق: فشل نشر الرد. الكود: {response.status_code}، التفاصيل: {response.text}")
        return False
        
    except Exception as e:
        print(f"🚨 خطأ هندسي في القسم (ذ) أثناء معالجة رد التعليق: {e}")
        return False
# ------------------------------------------------------------------
# القسم (ر): دالة إرسال رسالة تفاعلية تحتوي على أزرار (مثل زر تأكيد الطلب)
# ------------------------------------------------------------------

def send_messenger_buttons(fb_user_id: str, page_access_token: str, text_header: str) -> bool:
    """
    التسلسل الوظيفي 10:
    تأخذ معرف الزبون والتوكن، وترسل رسالة مجهزة بزر تفاعلي (Button Template).
    عندما يضغط الزبون على زر "تأكيد الطلب 📥"، يرسل فيسبوك حدث postback يحمل الكلمة CONFIRM_ORDER.
    """
    fb_url = f"https://facebook.com{page_access_token}"
    
    # الهيكل المعياري من مطوري ميتا لصناعة الرسائل ذات الأزرار
    payload = {
        "recipient": {"id": str(fb_user_id)},
        "message": {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": text_header, # النص الترحيبي الذي يظهر فوق الأزرار
                    "buttons": [
                        {
                            "type": "postback",
                            "title": "تأكيد الطلب 📥",
                            "payload": "CONFIRM_ORDER" # الشفرة السرية التي ستصل لسيرفرنا عند الضغط
                        }
                    ]
                }
            }
        }
    }
    
    try:
        response = requests.post(fb_url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"🚨 خطأ في القسم (ر) أثناء إرسال أزرار التأكيد: {e}")
        return False
# ------------------------------------------------------------------
# القسم (ز): دالة طلب بيانات الشحن والموقع الجغرافي من الزبون
# ------------------------------------------------------------------

def ask_for_customer_details(fb_user_id: str, page_access_token: str) -> bool:
    """
    التسلسل الوظيفي 11:
    تُستدعى هذه الدالة فوراً عندما يضغط الزبون على زر التأكيد.
    ترسل له رسالة تطلب معلوماته، مع إرفاق زر سريع (Quick Reply) يسمح له بمشاركة موقعه الجغرافي بضغطة زر.
    """
    fb_url = f"https://facebook.com{page_access_token}"
    
    # الهيكل المخصص لطلب الموقع والبيانات عبر الـ Quick Replies
    payload = {
        "recipient": {"id": str(fb_user_id)},
        "message": {
            "text": "يسعدنا جداً تأكيد طلبك! ✨ فضلاً، قم بكتابة (اسمك الكامل + رقم هاتفك) في رسالة واحدة، واضغط على الزر أدناه لمشاركتنا موقعك الجغرافي لتسهيل عملية التوصيل 🚚:",
            "quick_replies": [
                {
                    "content_type": "location" # هذا الزر يفتح الـ GPS في هاتف الزبون تلقائياً ويرسل إحداثياته للبوت
                }
            ]
        }
    }
    
    try:
        response = requests.post(fb_url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"🚨 خطأ في القسم (ز) أثناء طلب معلومات الشحن: {e}")
        return False
# ==========================================
# SECTION 3: FASTAPI ENDPOINTS (بوابات السيرفر)
# ==========================================

# ------------------------------------------------------------------
# القسم (أ): بوابة التحقق والمصافحة الأمنية مع فيسبوك (GET Webhook)
# ------------------------------------------------------------------

@app.get("/webhook")
async def verify_facebook_webhook(request: Request):
    """
    الوظيفة المعمارية 1 في القسم 3:
    تستقبل طلب التحقق يدوياً أو آلياً من منصة Meta Developers.
    تقرأ المعلمات القادمة في الرابط، وتتحقق من مطابقة الكلمة السرية (VERIFY_TOKEN).
    إذا تطابقت، تُرجع قيمة الـ challenge كنص نقي لتفعيل الاتصال فوراً.
    """
    # استخراج معلمات الاستعلام (Query Parameters) القادمة من فيسبوك
    query_params = request.query_params
    
    mode = query_params.get("hub.mode")          # يجب أن تكون قيمته دائماً 'subscribe'
    token = query_params.get("hub.verify_token") # الكلمة السرية المرسلة من فيسبوك وفحصها
    challenge = query_params.get("hub.challenge") # الشفرة العشوائية التي يطلب فيسبوك إرجاعها لها
    
    # التحقق الهندسي والأمني من هوية الطلب
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("🎉 [نجاح معماري]: تمت المصافحة والتحقق من فيسبوك بنجاح تام!")
            # العرف البرمجي يقتضي إرجاع الـ challenge كـ Plain Text نقي ليفهمه فيسبوك
            return Response(content=challenge, media_type="text/plain")
        else:
            # إرجاع خطأ 403 (ممنوع) إذا كانت الكلمة السرية خاطئة لحماية السيرفر من المتطفلين
            print("🚨 [تنبيه أمني]: محاولة اتصال بالـ Webhook بكلمة سرية خاطئة!")
            raise HTTPException(status_code=403, detail="Verification token mismatch")
            
    # رد افتراضي إذا قام شخص بفتح الرابط من المتصفح العادي
    return Response(content="SaaS Webhook Endpoint is running perfectly. Waiting for Meta signals...", media_type="text/plain")
# ------------------------------------------------------------------
# القسم (ب) [الجزء الأول]: بوابة استقبال البيانات وتفحص هوية الصفحة المشتركة
# ------------------------------------------------------------------

@app.post("/webhook")
async def handle_facebook_events(request: Request):
    """
    الوظيفة المعمارية 2 في القسم 3:
    تستقبل البيانات الخام (JSON) القادمة من فيسبوك عند حدوث أي تفاعل حقيقي،
    وتقوم بفرز الحدث والتأكد من أن الصفحة تابعة لتاجر مسجل لدينا في الساس.
    """
    # 1. قراءة واستخراج ملف الـ JSON الخام القادم من شبكة الإنترنت
    body = await request.json()
    
    # طباعة الحدث في الـ Terminal الخاص بـ Render لمراقبة البيانات الحية (Logs)
    print("📥 حدث جديد قادم من سيرفرات فيسبوك:", body)
    
    # 2. الفحص الأمني الأول: التأكد من أن الحدث قادم من صفحة (Page Object) وليس حساب شخصي
    if body.get("object") != "page":
        return {"status": "NOT_A_PAGE_EVENT"}
        
    # 3. الدخول في مصفوفة الأحداث (فيسبوك قد يرسل أكثر من حدث في نفس الجزء ث ثواني)
        # 3. الدخول في مصفوفة الأحداث
    for entry in body.get("entry", []):
        page_id = entry.get("id") # معرف صفحة الفيسبوك المستهدفة
        
        # 4. الاستدعاء الوظيفي: التحقق من اشتراك التاجر (يُرجع قاموساً أو None)
        merchant_page = check_page_subscription(page_id)
        
        if not merchant_page:
            # إذا كانت الصفحة غير مسجلة، نتخطاها فوراً لحماية موارد المعالج
            continue
            
        # التصحيح: القراءة مباشرة من القاموس دون استخدام [0]
        user_id = merchant_page.get("user_id") 
        
        # 5. الاستدعاء الوظيفي: فك تشفير التوكن الخاص بالصفحة
        try:
            page_access_token = get_decrypted_token(merchant_page.get("page_access_token"))
        except Exception:
            continue

        # 6. الاستدعاء الوظيفي: جلب معلومات العمل والاتصال
        business_info = get_business_profile(user_id)
        
        # 7. جلب إعدادات البوت الشخصية
        bot_query = supabase.table("bot_settings").select("system_instruction, temperature").eq("page_id", str(page_id)).execute()
        
        # فحص أمان مضاف: للتأكد من أن مصفوفة bot_query.data ليست فارغة قبل القراءة منها لمنع KeyError آخر
        if bot_query.data and len(bot_query.data) > 0:
            system_instruction = bot_query.data[0].get("system_instruction", "أنت مساعد ذكي لخدمة العملاء.")
            temperature = bot_query.data[0].get("temperature", 0.3)
        else:
            system_instruction = "أنت مساعد ذكي لخدمة العملاء."
            temperature = 0.3

        # ------------------------------------------------------------------
        # المحطة القادمة [الجزء الثاني]: فرز ومعالجة رسائل المسنجر (Messaging)
        # ------------------------------------------------------------------
        
    return {"status": "EVENT_PROCESSED"}
       
        # ------------------------------------------------------------------
        # القسم (ب) [الجزء الثاني]: فرز ومعالجة رسائل المسنجر والأزرار التفاعلية
        # ------------------------------------------------------------------
        
        # الفحص المعماري: هل يحتوي الحدث القادم على مصفوفة مراسلات (messaging)؟
    if "messaging" in entry:
            for message_event in entry["messaging"]:
                sender_id = str(message_event["sender"]["id"]) # معرف الزبون الفريد على فيسبوك
                
                # الفحص الأمني الحاسم: تجنب الرد على الرسائل الصادرة من البوت نفسه لمنع الحلقة اللانهائية (Loop)
                if sender_id == str(page_id):
                    continue

                # --- [1] معالجة الرسائل النصية العادية القادمة من الزبائن ---
                if "message" in message_event and "text" in message_event["message"]:
                    user_message = message_event["message"]["text"]
                    
                    # خطوة 1 (التسلسل الوظيفي 4): تشغيل مؤشر الكتابة فوراً لتهدئة وشراء وقت للزبون
                    send_typing_indicator(sender_id, page_access_token, "typing_on")
                    
                    # خطوة 2 (التسلسل الوظيفي 7): تدوين وحفظ رسالة الزبون الجديدة حياً في قاعدة البيانات
                    save_chat_to_history(page_id, sender_id, "user", user_message)
                    
                    # خطوة 3 (التسلسل الوظيفي 5): استرجاع سياق آخر 10 رسائل لإنعاش الذاكرة
                    chat_history = get_chat_context(page_id, sender_id, limit=10)
                    
                    # خطوة 4 (التسلسل الوظيفي 6): تمرير المعطيات كلها لـ Gemini 2.5 Flash للرد بفصحى ذكية ومؤمنة من الهلوسة
                    ai_response = ask_gemini_bot(
                        system_instruction=system_instruction,
                        business_info=business_info,
                        chat_history=chat_history,
                        new_message=user_message,
                        temperature=temperature
                    )
                    
                    # خطوة 5 (التسلسل الوظيفي 7): تدوين رد البوت الذكي في السجل قبل إرساله لضمان التزامن
                    save_chat_to_history(page_id, sender_id, "bot", ai_response)
                    
                    # خطوة 6 (التسلسل الوظيفي 8): قذف الإجابة النهائية المكتوبة بذكاء إلى هاتف الزبون
                    send_messenger_message(sender_id, page_access_token, ai_response)
                    
                    # خطوة 7 والأخيرة: إطفاء مؤشر الكتابة بعد إتمام المهمة بنجاح
                    send_typing_indicator(sender_id, page_access_token, "typing_off")

                # --- [2] معالجة نبضات الأزرار التفاعلية (Postbacks) عند ضغط العميل ---
                elif "postback" in message_event:
                    postback_payload = message_event["postback"]["payload"]
                    
                    # إذا كانت الشفرة السرية للزر المضغوط هي تأكيد الشراء
                    if postback_payload == "CONFIRM_ORDER":
                        # خطوة 1: تشغيل مؤشر الكتابة فوراً
                        send_typing_indicator(sender_id, page_access_token, "typing_on")
                        
                        # خطوة 2 (التسلسل الوظيفي 11): إرسال رسالة طلب البيانات الفورية وتفعيل الـ GPS للموقع الجغرافي
                        ask_for_customer_details(sender_id, page_access_token)
                        
                        # خطوة 3: إطفاء مؤشر الكتابة
                        send_typing_indicator(sender_id, page_access_token, "typing_off")
        # ------------------------------------------------------------------
        # القسم (ب) [الجزء الثالث]: فرز ومعالجة تعليقات المنشورات (Feed / Comments)
        # ------------------------------------------------------------------
        
        # الفحص المعماري: هل يحتوي الحدث القادم على مصفوفة تغييرات الجدار (changes)؟
    elif "changes" in entry:
            for change in entry["changes"]:
                # التأكد من أن التغيير حدث في جدار الصفحة (feed)
                if change.get("field") == "feed":
                    value = change.get("value", {})
                    
                    # فحص دقيق: نريد التفاعل فقط إذا كان الحدث هو إضافة تعليق جديد (comment add)
                    if value.get("item") == "comment" and value.get("verb") == "add":
                        comment_id = str(value.get("comment_id")) # معرف التعليق الفريد
                        comment_text = value.get("message")       # النص الذي كتبه الزبون في التعليق
                        sender_id = str(value.get("from", {}).get("id")) # معرف كاتب التعليق
                        
                        # الفحص الأمني الصارم: تجنب الرد على تعليقات الصفحة نفسها لمنع الحلقات التكرارية
                        if sender_id == str(page_id):
                            continue
                            
                        # صياغة طلب مخصص للتعليقات لتوجيه Gemini للرد بأسلوب تسويقي فصيح ومختصر
                        comment_prompt = (
                            f"{system_instruction}\n{business_info}\n\n"
                            f"قام أحد العملاء بالتعليق على منشورنا بالعبارة التالية: '{comment_text}'\n"
                            f"التعليمات: اكتب رداً تسويقياً جذاباً، لابقاً، وقصيراً جداً باللغة العربية الفصحى "
                            f"للرد عليه مباشرة كتعليق، دون زيادة أو تأليف معلومات غير موجودة مسبقاً. اكتب الرد مباشرة:"
                        )
                        
                        try:
                            # توليد الرد عبر عميل جوجل الموحد والحديث
                            response = ai_client.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=comment_prompt,
                                config={'temperature': temperature}
                            )
                            ai_comment_reply = response.text.strip()
                            
                            # (التسلسل الوظيفي 9): إرسال الرد النهائي كتعليق متصل ومباشر على فيسبوك
                            send_facebook_comment_reply(comment_id, page_access_token, ai_comment_reply)
                            
                        except Exception as e:
                            print(f"🚨 خطأ في الجزء الثالث أثناء توليد أو إرسال رد التعليق: {e}")
# ==========================================
# كتلة التشغيل الرئيسية (MAIN EXECUTION BLOCK)
# ==========================================

if __name__ == "__main__":
    import uvicorn
    # تشغيل السيرفر تلقائياً عند استدعاء الملف مباشرة
    # يفتح السيرفر محلياً على المنفذ 8000 مع تفعيل خاصية الـ reload لتسهيل التطوير
    uvicorn.run("fb:app", host="127.0.0.1", port=8000, reload=True)
