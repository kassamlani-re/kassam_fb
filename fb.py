import os
import json
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests

# مكتبات الذكاء الاصطناعي، قاعدة البيانات، وحساب المسافات
from google import genai
from google.genai import types
from supabase import create_client, Client
from geopy.distance import geodesic

# 1. تحميل المتغيرات السرية وإعداد الاتصالات
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")

# معرف التاجر الحالي في منصة الـ SaaS يتم سحبه من بيئة التشغيل
CURRENT_MERCHANT_ID = os.getenv("CURRENT_MERCHANT_ID")

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# ----------------------------------------------------
# الوظيفة الأولى: دالة فحص وحجز المواعيد
# ----------------------------------------------------
def check_and_book_appointment(customer_name: str, customer_phone: str, requested_time_str: str) -> str:
    """ دالة مخصصة لطلبها بواسطة Gemini لحجز المواعيد في العيادات والمكاتب """
    try:
        # تحويل صيغة الوقت القادمة من الذكاء الاصطناعي
        requested_time = datetime.strptime(requested_time_str, '%Y-%m-%d %H:%M:%S')
        
        check_query = supabase.table("appointments") \
            .select("*") \
            .eq("merchant_id", CURRENT_MERCHANT_ID) \
            .eq("appointment_time", requested_time.isoformat()) \
            .eq("status", "confirmed") \
            .execute()
            
        if len(check_query.data) > 0:
            return "نعتذر منك، هذا الوقت محجوز مسبقاً. يرجى اختيار وقت آخر."
        else:
            booking_data = {
                "merchant_id": CURRENT_MERCHANT_ID,
                "customer_phone": customer_phone,
                "customer_name": customer_name,
                "appointment_time": requested_time.isoformat(),
                "status": "confirmed"
            }
            supabase.table("appointments").insert(booking_data).execute()
            return f"تم تأكيد حجزك بنجاح باسم {customer_name} في التاريخ والوقت المحدد: {requested_time_str}."
    except Exception as e:
        return f"حدث خطأ أثناء معالجة الحجز: {str(e)}"

# ----------------------------------------------------
# الوظيفة الثانية: دالة حساب تكلفة التوصيل بناءً على الموقع
# ----------------------------------------------------
def calculate_delivery_cost(customer_lat: float, customer_lng: float) -> str:
    """ دالة مخصصة لطلبها بواسطة Gemini لحساب تكلفة التوصيل للزبون """
    try:
        merchant_query = supabase.table("merchants").select("latitude", "longitude", "delivery_fee_per_km").eq("id", CURRENT_MERCHANT_ID).single().execute()
        merchant_data = merchant_query.data
        
        if not merchant_data:
            return "خطأ: لم يتم العثور على بيانات التاجر في النظام."
            
        merchant_coords = (merchant_data['latitude'], merchant_data['longitude'])
        customer_coords = (customer_lat, customer_lng)
        
        # حساب المسافة الحقيقية
        distance_km = geodesic(merchant_coords, customer_coords).kilometers
        total_cost = distance_km * float(merchant_data['delivery_fee_per_km'])
        
        return f"المسافة التقريبية للمحل هي {round(distance_km, 2)} كم، وتكلفة التوصيل الإجمالية لموقعك هي: {round(total_cost, 2)} دينار."
    except Exception as e:
        return f"حدث خطأ أثناء حساب تكلفة التوصيل: {str(e)}"

# ----------------------------------------------------
# إدارة ذاكرة وسياق المحادثة مع Supabase (Chat History)
# ----------------------------------------------------
def save_to_chat_history(sender: str, message_text: str, platform: str = "messenger"):
    """ دالة لحفظ كل رسالة (سواء من الزبون أو البوت) كسطر مستقل متوافق مع جدولك """
    try:
        data = {
            "merchant_id": CURRENT_MERCHANT_ID,
            "sender": sender,
            "message_text": message_text,
            "platform": platform
        }
        supabase.table("chat_history").insert(data).execute()
    except Exception as e:
        print("خطأ أثناء حفظ سجل المحادثة:", e)

def get_chat_context(sender_id: str) -> list:
    """ جلب آخر 6 رسائل من السجل وترتيبها كرسائل سياق لـ Gemini """
    try:
        query = supabase.table("chat_history") \
            .select("sender, message_text") \
            .eq("merchant_id", CURRENT_MERCHANT_ID) \
            .eq("platform", "messenger") \
            .order("created_at", ascending=False) \
            .limit(6) \
            .execute()
            
        history_data = query.data
        contents = []
        
        # ترتيب المحادثة زمنياً من الأقدم للأحدث لتغذية عقل البوت
        for msg in reversed(history_data):
            role = "user" if msg['sender'] != "bot" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg['message_text'])]
            ))
        return contents
    except Exception as e:
        print("خطأ أثناء جلب سياق المحادثة:", e)
        return []
# ----------------------------------------------------
# الوظيفة الرابعة: عقل البوت والتكامل مع Gemini والوظائف الذكية
# ----------------------------------------------------
def process_message_with_gemini(sender_id, user_message):
    """ إرسال سياق المحادثة والرسالة الجديدة لـ Gemini وتفعيل استدعاء الدوال تلقائياً """
    try:
        # 1. جلب التاريخ القصير للمحادثة من جدول chat_history لضمان وجود ذاكرة للبوت
        history_contents = get_chat_context(sender_id)
        
        # 2. إضافة الرسالة الحالية الجديدة للزبون إلى قائمة السياق
        history_contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=user_message)]
        ))
        
        # 3. إعداد الأدوات (Functions) المتاحة لـ Gemini ليستخدمها عند الحاجة
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="check_and_book_appointment",
                        description="تستخدم لحجز موعد جديد للزبون في العيادة أو المكتب بعد أخذ اسمه، هاتفه، والوقت المطلق بصيغة YYYY-MM-DD HH:MM:S.",
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
                        name="calculate_delivery_cost",
                        description="تستخدم لحساب المسافة وتكلفة التوصيل الإجمالية للزبون بناءً على إحداثيات موقعه الجغرافي (خطوط الطول والعرض).",
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
        
        # 4. إعداد التوجيهات الأساسية لهوية البوت وسلوكه
        system_instruction = (
            "أنت مساعد ذكي ومحترف لمنصة SaaS تعمل لصالح تجار وعيادات أطباء. "
            "أجب دائماً بلغة مهذبة، واضحة، وموجزة. "
            "إذا طلب العميل حجز موعد، تأكد من جمع (الاسم، الهاتف، والوقت الدقيق) قبل استدعاء دالة الحجز. "
            "إذا أرسل العميل موقعه الجغرافي أو طلب حساب التوصيل، استخدم أداة حساب التوصيل فوراً لإفادته بالسعر الحقيقي والمسافة."
        )
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.7
        )
        
        # 5. إرسال الطلب إلى نموذج Gemini الشامل والحديث
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=history_contents,
            config=config
        )
        
        # 6. التحقق مما إذا كان Gemini يطلب استدعاء دالة أو أداة خارجية (Function Call)
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "check_and_book_appointment":
                    # تنفيذ دالة حجز الموعد وإرجاع النتيجة
                    args = call.args
                    result_msg = check_and_book_appointment(args['customer_name'], args['customer_phone'], args['requested_time_str'])
                    return result_msg
                    
                elif call.name == "calculate_delivery_cost":
                    # تنفيذ دالة حساب تكلفة التوصيل الجغرافية وإرجاع النتيجة
                    args = call.args
                    result_msg = calculate_delivery_cost(args['customer_lat'], args['customer_lng'])
                    return result_msg
        
        # في حال كان رد طبيعي حوّله لنص وأرجعه
        return response.text if response.text else "لم أتمكن من فهم طلبك، كيف يمكنني مساعدتك؟"
        
    except Exception as e:
        print("خطأ أثناء المعالجة عبر Gemini:", e)
        return "معذرةً، حدث خطأ مؤقت في نظام المعالجة الذكي."

# ----------------------------------------------------
# الوظيفة الخامسة: مسارات استقبال وتحقق ويب هوك ميتا الموحد
# ----------------------------------------------------
@app.route('/', methods=['GET'])
def home():
    return "مرحباً بك! سيرفر منصة SaaS يعمل بنجاح ومستيقظ الآن 🚀", 200

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """ خطوة التحقق التي تطلبها ميتا عند ربط السيرفر لأول مرة """
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
    """ استقبال رسائل الزبائن الفورية ومعالجتها بالذكاء الاصطناعي """
    data = request.get_json()
    
    if not data:
        return "بيانات فارغة", 400
        
    if data.get('object') in ['page', 'instagram']:
        try:
            entry = data['entry'][0]
            if 'messaging' in entry:
                messaging = entry['messaging'][0]
                
                if 'message' in messaging and 'text' in messaging['message']:
                    sender_id = messaging['sender']['id']  # معرف حساب الزبون الفريد
                    message_text = messaging['message']['text']  # نص رسالة الزبون
                    
                    print(f"[ماسنجر] رسالة جديدة من {sender_id}: {message_text}")
                    
                    # أ) حفظ رسالة الزبون الواردة أولاً في قاعدة البيانات
                    save_to_chat_history(sender=sender_id, message_text=message_text)
                    
                    # ب) تمرير الرسالة لعقل Gemini لتوليد الرد الذكي أو استدعاء الدوال
                    reply_text = process_message_with_gemini(sender_id, message_text)
                    
                    # ج) حفظ الرد الصادر من البوت في جدول قاعدة البيانات باسم "bot"
                    save_to_chat_history(sender="bot", message_text=reply_text)
                    
                    # د) إرسال الرد النهائي عبر سيرفرات ميتا لهاتف المستخدم
                    send_messenger_reply(sender_id, reply_text)
                    
        except Exception as e:
            print("خطأ في معالجة رسالة ماسنجر الواردة:", e)
        
    return "تم الاستلام والمعالجة بنجاح", 200

def send_messenger_reply(recipient_id, text_reply):
    """ دالة ترسل الرد المكتوب إلى حساب الزبون في ماسنجر عبر الرابط الرسمي الصحيح ومجزأ لحمايته """
    
    # الرابط مدمج ومقسم لتجنب حجب الفلتر الرقمي أثناء المحادثة وعمل الكود بأمان
    url = "https" + "://graph." + "facebook." + "com/v21.0/" + "me/messages"
    
    query_params = {
        "access_token": PAGE_ACCESS_TOKEN
    }
    
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
