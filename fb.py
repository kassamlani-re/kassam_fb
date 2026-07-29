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

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# ----------------------------------------------------
# الوظائف المساعدة الديناميكية (ربط قاعدة البيانات بالتاجر الممرر)
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
        return f"خطأ في حساب تكلفة التوصيل: {str(e)}"

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
            return "نعتذر منك، هذا الوقت محجوز مسبقاً. يرجى اختيار وقت آخر."
            
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
    """ إرسال سياق نظيف وإجبار الموديل على الالتزام ببيانات التاجر المستخرج تلقائياً """
    try:
        merchant_id = merchant_data.get('id')
        merchant_name = merchant_data.get('business_name', 'المحل')
        merchant_phone = merchant_data.get('phone_number', 'غير مسجل')
        business_type = merchant_data.get('business_type', 'خدماتي')

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
        
        system_instruction = (
            f"أنت المساعد الافتراضي الآلي لصفحة ({merchant_name}) المتخصصة في ({business_type}). "
            f"معلومات التواصل الحقيقية والوحيدة الخاصة بنا هي: رقم الهاتف الحركي هو ({merchant_phone}). "
            "التزم بهذا الرقم حرفياً إذا سألك الزبون عن الهاتف، وممنوع اختراع أو تخمين أي أرقام أخرى نهائياً. "
            "ممنوع تماماً أن تطلب من الزبون كتابة أرقام إحداثيات أو أرقام جغرافية يدوياً. "
            "إذا طلب حساب التوصيل، قل له: (يرجى الضغط على زر مشاركة الموقع من المرفقات لتصلنا إحداثياتك تلقائياً). "
            "أجب باختصار شديد، بأسلوب خدمة عملاء محترف وودود، ولا تذكر جوجل أو منصة SaaS."
        )
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.1,
            max_output_tokens=200
        )
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=history_contents,
            config=config
        )
        
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "check_and_book_appointment_dynamic":
                    args = call.args
                    return check_and_book_appointment_dynamic(merchant_id, args['customer_name'], args['customer_phone'], args['requested_time_str'])
                elif call.name == "calculate_delivery_cost_dynamic":
                    args = call.args
                    return calculate_delivery_cost_dynamic(merchant_data, args['customer_lat'], args['customer_lng'])
        
        return response.text if response.text else "مرحباً بك، كيف يمكنني مساعدتك؟"
        
    except Exception as e:
        print("خطأ في المعالجة عبر Gemini:", e)
        return "معذرةً، حدث خطأ مؤقت في نظام المعالجة الذكي."
# ----------------------------------------------------
# مسارات استقبال وتحقق ويب هوك ميتا الموحد
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
    """ استقبال الرسائل والمواقع الجغرافية والتعرف على التاجر ديناميكياً """
    data = request.get_json()
    if not data:
        return "بيانات فارغة", 400
        
    if data.get('object') in ['page', 'instagram']:
        try:
            entry = data['entry'][0]
            # التقاط معرف الصفحة التي استقبلت الرسالة الآن تلقائياً
            facebook_page_id = entry.get('id') 
            
            # الاستعلام الفوري في قاعدة البيانات لمعرفة من هو التاجر صاحب هذه الصفحة
            merchant_query = supabase.table("merchants").select("*").eq("facebook_page_id", str(facebook_page_id)).single().execute()
            merchant_data = merchant_query.data
            
            if not merchant_data:
                print(f"تحذير: وصل طلب لصفحة فيسبوك غير مسجلة في نظامنا: {facebook_page_id}")
                return "صفحة غير مسجلة", 200
                
            merchant_id = merchant_data['id']
            
            if 'messaging' in entry:
                messaging = entry['messaging'][0]
                sender_id = messaging['sender']['id']
                
                # 1. التقاط الموقع الجغرافي تلقائياً وحساب التوصيل للتاجر الحالي
                if 'message' in messaging and 'attachments' in messaging['message']:
                    for attachment in messaging['message']['attachments']:
                        if attachment.get('type') == 'location':
                            coordinates = attachment['payload']['coordinates']
                            lat = coordinates['lat']
                            lng = coordinates['long']
                            
                            save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل موقعه الجغرافي]")
                            reply_text = calculate_delivery_cost_dynamic(merchant_data, lat, lng)
                            
                            save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                            send_messenger_reply(sender_id, reply_text)
                            return "تم المعالجة", 200

                # 2. استقبال الرسائل النصية العادية وتمريرها لعقل Gemini مع بيانات التاجر التلقائية
                if 'message' in messaging and 'text' in messaging['message']:
                    message_text = messaging['message']['text']
                    
                    save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text=message_text)
                    reply_text = process_message_with_gemini(sender_id, message_text, merchant_data)
                    
                    save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                    send_messenger_reply(sender_id, reply_text)
                    
        except Exception as e:
            print("خطأ في معالجة رسالة ماسنجر الواردة:", e)
        
    return "تم الاستلام والمعالجة بنجاح", 200

def send_messenger_reply(recipient_id, text_reply):
    """ دالة ترسل الرد المكتوب إلى حساب الزبون في ماسنجر عبر الرابط الرسمي الصحيح ومجزأ لحمايته """
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
