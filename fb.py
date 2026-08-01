import os
import json
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests

# مكتبات الذكاء الاصطناعي، قاعدة البيانات، وحساب المسافات والتشفير
from google import genai
from google.genai import types
from supabase import create_client, Client
from geopy.distance import geodesic
from cryptography.fernet import Fernet

# 1. تحميل المتغيرات السرية وإعداد الاتصالات
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")

# جلب مفتاح التشفير السري الموحد المحمي في سيرفر Render لخدمة كل التجار
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("خطأ حرج: لم يتم العثور على ENCRYPTION_KEY في متغيرات البيئة بسيرفر Render!")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# ----------------------------------------------------
# وظائف الأمان والتشفير البرمجي الموحد لبيئة الـ SaaS
# ----------------------------------------------------
def encrypt_token(plain_token: str) -> str:
    """ تشفير توكن التاجر قبل إرساله وحفظه في Supabase """
    if not plain_token: return ""
    return cipher_suite.encrypt(plain_token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """ فك تشفير توكن التاجر عند سحبه من Supabase للاستخدام الفوري بالذاكرة """
    if not encrypted_token: return ""
    try:
        return cipher_suite.decrypt(encrypted_token.encode()).decode()
    except Exception as e:
        print("خطأ حرج في فك التشفير، قد يكون المفتاح السري غير متطابق:", e)
        return ""

# ----------------------------------------------------
# الوظائف المساعدة الديناميكية المعزولة لكل تاجر
# ----------------------------------------------------
def save_to_chat_history_dynamic(merchant_id, sender: str, message_text: str):
    """ حفظ كل رسالة كسطر مستقل مربوط بالتاجر المكتشف تلقائياً """
    try:
        data = {
            "merchant_id": merchant_id, 
            "sender": sender, 
            "message_text": message_text, 
            "platform": "messenger"
        }
        supabase.table("chat_history").insert(data).execute()
    except Exception as e: 
        print("خطأ في حفظ سجل المحادثة:", e)

def get_chat_context(sender_id: str, merchant_id: str) -> list:
    """ جلب الذاكرة وتصفيها برمجياً حسب معرف التاجر لـ Gemini """
    try:
        query = supabase.table("chat_history") \
            .select("sender, message_text") \
            .eq("merchant_id", merchant_id) \
            .eq("platform", "messenger") \
            .order("created_at", ascending=False) \
            .limit(6) \
            .execute()
        contents = []
        for msg in reversed(query.data):
            role = "user" if msg['sender'] != "bot" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(msg['message_text']))]))
        return contents
    except Exception as e: 
        print("خطأ في سحب الذاكرة:", e)
        return []

def calculate_delivery_cost_dynamic(merchant_data, customer_lat: float, customer_lng: float) -> str:
    """ حساب المسافة والتوصيل من إحداثيات محل التاجر المسترجع حياً """
    try:
        merchant_coords = (merchant_data['latitude'], merchant_data['longitude'])
        distance_km = geodesic(merchant_coords, (customer_lat, customer_lng)).kilometers
        total_cost = distance_km * float(merchant_data['delivery_fee_per_km'])
        return f"المسافة التقريبية للمحل هي {round(distance_km, 2)} كم، وتكلفة التوصيل الإجمالية لموقعك هي: {round(total_cost, 2)} دينار."
    except Exception as e: 
        return f"خطأ في حساب تكلفة التوصيل الجغرافي: {str(e)}"
def check_and_book_appointment_dynamic(merchant_id, customer_name: str, customer_phone: str, requested_time_str: str) -> str:
    """ حجز المواعيد في جدول التاجر الصحيح ومنع التضارب زمنياً """
    try:
        requested_time = datetime.strptime(requested_time_str, '%Y-%m-%d %H:%M:%S')
        check_query = supabase.table("appointments") \
            .select("*") \
            .eq("merchant_id", merchant_id) \
            .eq("appointment_time", requested_time.isoformat()) \
            .eq("status", "confirmed") \
            .execute()
            
        if len(check_query.data) > 0: 
            return "نعتذر منك، هذا الوقت محجوز مسبقاً من قبل زبون آخر. يرجى اختيار وقت وتاريخ آخر."
            
        booking_data = {
            "merchant_id": merchant_id, 
            "customer_phone": customer_phone, 
            "customer_name": customer_name, 
            "appointment_time": requested_time.isoformat(), 
            "status": "confirmed"
        }
        supabase.table("appointments").insert(booking_data).execute()
        return f"تم تأكيد حجزك بنجاح باسم {customer_name} في الوقت والتاريخ المحدد: {requested_time_str}."
    except Exception as e: 
        return f"خطأ أثناء معالجة حجز الموعد: {str(e)}"

# ----------------------------------------------------
# عقل البوت والتكامل مع Gemini والوظائف الذكية
# ----------------------------------------------------
def process_message_with_gemini(sender_id, user_message, merchant_data):
    """ إرسال سياق نظيف وإجبار الموديل على الالتزام الصارم ببيانات التاجر وحالة التوصيل """
    try:
        merchant_id = merchant_data.get('id')
        merchant_name = merchant_data.get('business_name', 'المحل')
        merchant_phone = merchant_data.get('phone_number', 'غير مسجل')
        business_type = merchant_data.get('business_type', 'خدماتي')
        
        # التحقق من حالة التوصيل للتاجر (True أو False من قاعدة البيانات)
        provides_delivery = merchant_data.get('provides_delivery', False)

        # جلب الذاكرة الصافية للزبون مع هذا التاجر بالذات
        history_contents = get_chat_context(sender_id, merchant_id)
        
        history_contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=str(user_message))]
        ))
        
        # تفعيل الأدوات والوظائف الذكية لـ Gemini
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="check_and_book_appointment_dynamic",
                        description="تستخدم لحجز موعد جديد للزبون في العيادة أو المكتب بعد أخذ اسمه، هاتفه، والوقت المطلوب بصيغة YYYY-MM-DD HH:MM:SS.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_name": types.Schema(type=types.Type.STRING, description="اسم الزبون الكامل"),
                                "customer_phone": types.Schema(type=types.Type.STRING, description="رقم هاتف الزبون الحركي"),
                                "requested_time_str": types.Schema(type=types.Type.STRING, description="الوقت والتاريخ بصيغة YYYY-MM-DD HH:MM:SS")
                            },
                            required=["customer_name", "customer_phone", "requested_time_str"]
                        )
                    ),
                    types.FunctionDeclaration(
                        name="calculate_delivery_cost_dynamic",
                        description="تستخدم لحساب المسافة وتكلفة التوصيل الإجمالية للزبون بناءً على إحداثيات موقعه الجغرافي.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_lat": types.Schema(type=types.Type.NUMBER, description="خط العرض لموقع الزبون Latitude"),
                                "customer_lng": types.Schema(type=types.Type.NUMBER, description="خط الطول لموقع الزبون Longitude")
                            },
                            required=["customer_lat", "customer_lng"]
                        )
                    )
                ]
            )
        ]
        
        # صياغة التعليمات الصارمة وحقن حالة التوصيل حياً لكل تاجر مستقل
        if provides_delivery:
            delivery_instruction = (
                "نحن نوفر خدمة التوصيل للموقع. إذا استفسر الزبون عن التوصيل أو سعره، "
                "يجب أن تجيبه حرفياً بالصيغة التالية: 'نعم، التوصيل متوفر. يمكنك إضافة موقعك الدقيق من خلال الضغط على زر مشاركة الموقع لتصلنا إحداثياتك تلقائياً وحساب التكلفة بدقة.' "
                "ممنوع تماماً اختراع أسعار توصيل، وممنوع طلب كتابة أرقام إحداثيات أو عناوين نصية يدوياً."
            )
        else:
            delivery_instruction = (
                "نحن لا نوفر خدمة التوصيل نهائياً في هذا المحل. إذا سألك الزبون عن التوصيل أو سعره، "
                "اعتذر له بلطف وبوضوح تام وأخبره أن الخدمة غير متوفرة حالياً وعليها الاستلام من مقرنا فقط."
            )
        
        system_instruction = (
            f"أنت المساعد الافتراضي الآلي لصفحة ({merchant_name}) المتخصصة في ({business_type}). "
            f"معلومات التواصل الحقيقية والوحيدة الخاصة بنا هي: رقم الهاتف الحركي هو ({merchant_phone}). "
            f"التزم بهذا الرقم حرفياً إذا سألك الزبون عن الهاتف، وممنوع اختراع أو تخمين أي أرقام أخرى نهائياً. "
            f"{delivery_instruction} "
            "أجب باختصار شديد، بأسلوب خدمة عملاء محترف وودود، ولا تذكر جوجل أو منصة SaaS في ردودك."
        )
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.1,  # التزام صارم بالحقائق الممررة من قاعدة البيانات ومنع التخريف الرقمي
            max_output_tokens=200
        )
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=history_contents,
            config=config
        )
        
        # معالجة استدعاء الدوال الذكية (Function Calling)
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "check_and_book_appointment_dynamic":
                    args = call.args
                    return check_and_book_appointment_dynamic(merchant_id, args['customer_name'], args['customer_phone'], args['requested_time_str'])
                elif call.name == "calculate_delivery_cost_dynamic":
                    if not provides_delivery:
                        return "معذرةً، خدمة التوصيل غير متوفرة لدينا حالياً."
                    args = call.args
                    return calculate_delivery_cost_dynamic(merchant_data, args['customer_lat'], args['customer_lng'])
        
        return response.text if response.text else "مرحباً بك، كيف يمكنني مساعدتك اليوم؟"
        
    except Exception as e:
        print("خطأ في المعالجة عبر Gemini:", e)
        return "معذرةً، حدث خطأ مؤقت في نظام المعالجة الذكي."
# ----------------------------------------------------
# ميزات الـ Sprint الجديد: مؤشر الكتابة وأزرار فيسبوك التفاعلية الديناميكية
# ----------------------------------------------------

def send_facebook_action_dynamic(recipient_id, access_token, action_type="typing_on"):
    """ تشغيل أو إيقاف مؤشر الكتابة بناءً على توكن التاجر برابط مفكك محمي من الفلاتر وقص الواجهة """
    # تفكيك الرابط نصياً بعلامة الزائد لمنع واجهة الشات من قصه وتحويله لرابط نشط
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "sender_action": action_type
    }
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers, params=query_params)
    except Exception as e:
        print(f"خطأ في إرسال إشارة فيسبوك ({action_type}):", e)


def send_location_button_dynamic(recipient_id, access_token, text_message):
    """ إرسال زر تفاعلي رسمي (Quick Reply) يطلب من الزبون إرسال موقعه الجغرافي بنقرة واحدة """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "text": text_message,
            "quick_replies": [
                {
                    "content_type": "location"
                }
            ]
        }
    }
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers, params=query_params)
    except Exception as e:
        print("خطأ في إرسال زر مشاركة الموقع التفاعلي:", e)


def send_messenger_reply_dynamic(recipient_id, text_reply, access_token):
    """ إرسال الرد النصي النهائي للزبون باستخدام توكن التاجر الصحيح ديناميكياً """
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    
    query_params = {"access_token": access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text_reply}
    }
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers, params=query_params)
        if response.status_code == 200:
            print("==> تم إرسال الرد العكسي للزبون عبر فيسبوك بنجاح! 🎉")
        else:
            print(f"رفض الإرسال من طرف ميتا (كود {response.status_code}): {response.text}")
    except Exception as e:
        print("حدث خطأ أثناء محاولة الاتصال بخوادم ميتا الرسمية:", e)


# ----------------------------------------------------
# مسارات استقبال وتحقق ويب هوك ميتا الموحد المحدثة الصارمة
# ----------------------------------------------------

@app.route('/', methods=['GET'])
def home():
    return "مرحباً بك! سيرفر منصة SaaS يعمل بنجاح ومستيقظ الآن 🚀", 200


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """ خطوة التحقق الآمنة مع ميتا عند ربط السيرفر لأول مرة """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode and token:
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("==> تم التحقق من السيرفر بنجاح من قبل ميتا!")
            return challenge, 200
        else:
            return "توكن غير متطابق", 403
    return "طلب غير صالح", 400


@app.route('/webhook', methods=['POST'])
def handle_messages():
    """ استقبال ومعالجة الرسائل والمواقع وحجز المواعيد بشكل ديناميكي معزول بالكامل لكل تاجر """
    data = request.get_json()
    if not data:
        return "بيانات فارغة", 400
        
    if data.get('object') in ['page', 'instagram']:
        try:
            # معالجة استخراج المصفوفات بشكل صارم ومضمون وفقاً لهيكلة ميتا الحية لمنع الـ Crash
            entry = data['entry'][0]
            if 'messaging' in entry:
                messaging = entry['messaging'][0]
                sender_id = messaging['sender']['id'] 
                facebook_page_id = messaging['recipient']['id']
                
                print(f"[نظام SaaS] تم التقاط معرف الصفحة المستقبلة حياً: {facebook_page_id}")
                
                # جلب بيانات التجار والمطابقة البرمجية في الذاكرة لتفادي انهيار السيرفر
                merchant_query = supabase.table("merchants").select("*").execute()
                merchant_data = None
                
                if merchant_query.data:
                    for merchant in merchant_query.data:
                        db_page_id = str(merchant.get('facebook_page_id', '')).strip()
                        incoming_page_id = str(facebook_page_id).strip()
                        
                        if db_page_id == incoming_page_id:
                            merchant_data = merchant
                            break
                
                if not merchant_data:
                    print(f"تحذير: لم نجد تاجر مطابق في الذاكرة للرقم: {facebook_page_id}")
                    return "صفحة غير مسجلة في النظام", 200
                    
                merchant_id = merchant_data['id']
                
                # سحب التوكن المشفر الخاص بالتاجر وفك تشفيره لحظياً في الذاكرة (حماية الـ SaaS)
                encrypted_token = merchant_data.get('page_access_token')
                merchant_token = decrypt_token(encrypted_token) if encrypted_token else PAGE_ACCESS_TOKEN

                # تشغيل مؤشر الكتابة فوراً بمجرد استقبال الرسالة لمنح شعور طبيعي للبشر
                send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_on")

                # 1. التقاط الموقع الجغرافي التلقائي وحساب التوصيل للتاجر الحالي
                if 'message' in messaging and 'attachments' in messaging['message']:
                    for attachment in messaging['message']['attachments']:
                        if attachment.get('type') == 'location':
                            coordinates = attachment['payload']['coordinates']
                            lat = coordinates['lat']
                            lng = coordinates['long']
                            
                            save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل موقعه الجغرافي]")
                            reply_text = calculate_delivery_cost_dynamic(merchant_data, lat, lng)
                            
                            save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                            
                            # إغلاق مؤشر الكتابة وإرسال الرد المالي النهائي
                            send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
                            send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                            return "تم المعالجة", 200

                # 2. استقبال الرسائل النصية وتمريرها لعقل Gemini مع بيانات التاجر التلقائية
                if 'message' in messaging and 'text' in messaging['message']:
                    message_text = messaging['message']['text']
                    
                    save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text=message_text)
                    reply_text = process_message_with_gemini(sender_id, message_text, merchant_data)
                    
                    save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                    
                    # إغلاق مؤشر الكتابة قبل الإرسال العكسي
                    send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
                    
                    # ذكاء التوصيل: إذا كان الرد يوجه الزبون للموقع، نرسل له الزر التفاعلي فوراً
                    if "زر مشاركة الموقع" in reply_text or "لتصلنا إحداثياتك تلقائياً" in reply_text:
                        send_location_button_dynamic(sender_id, merchant_token, reply_text)
                    else:
                        send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                    
        except Exception as e:
            print("خطأ في معالجة رسالة ماسنجر الواردة:", e)
        
    return "تم الاستلام والمعالجة بنجاح", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
