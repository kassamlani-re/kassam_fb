import os
import json
import time
import threading
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

# جلب مفتاح التشفير السري الموحد المحمي في سيرفر Render لخدمة كل التجار بأمان
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("خطأ حرج: لم يتم العثور على ENCRYPTION_KEY في متغيرات البيئة!")

cipher_suite = Fernet(ENCRYPTION_KEY.encode())
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")) 
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

# طبقة حماية إبادة التكرار الحية والمطابقة لسجلات سيرفر Render
PROCESSED_MESSAGE_IDS = set()

def clear_old_message_ids():
    """ دالة تنظيف تلقائية مستمرة في الخلفية لمنع امتلاء الذاكرة بالمُعرفات القديمة """
    while True:
        time.sleep(60)  # تنظيف ذاكرة المعرفات كل دقيقة
        PROCESSED_MESSAGE_IDS.clear()

# تشغيل خيط التنظيف التلقائي في الخلفية كمحرك صامت لطبقة الحماية
threading.Thread(target=clear_old_message_ids, daemon=True).start()

# ----------------------------------------------------
# وظائف الأمان والتشفير البرمجي الموحد لبيئة الـ SaaS
# ----------------------------------------------------
def decrypt_token(encrypted_token: str) -> str:
    """ فك تشفير توكن التاجر عند سحبه من Supabase للاستخدام الفوري بالذاكرة """
    if not encrypted_token: return ""
    try:
        return cipher_suite.decrypt(encrypted_token.encode()).decode()
    except Exception as e:
        print("خطأ حرج في فك التشفير:", e)
        return ""
# ----------------------------------------------------
# الوظائف المساعدة الديناميكية المعزولة لكل تاجر
# ----------------------------------------------------
def save_to_chat_history_dynamic(merchant_id, sender: str, message_text: str):
    """ حفظ كل رسالة كسطر مستقل مربوط بالتاجر المكتشف تلقائياً وبشكل اختياري لرقم الهاتف """
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
    """ جلب الذاكرة السياقية وتصفيتها وقلبها عبر بايثون لتفادي مشاكل دالة الترتيب في Supabase """
    try:
        query = supabase.table("chat_history") \
            .select("sender, message_text") \
            .eq("merchant_id", merchant_id) \
            .eq("platform", "messenger") \
            .limit(6) \
            .execute()
        contents = []
        # قلب المصفوفة في الذاكرة لترتيبها من الأقدم للأحدث بأمان كامل
        for msg in reversed(query.data):
            role = "user" if msg['sender'] != "bot" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(msg['message_text']))]))
        return contents
    except Exception as e: 
        print("خطأ في سحب الذاكرة السياقية:", e)
        return []

def calculate_delivery_cost_dynamic(merchant_data, customer_lat: float, customer_lng: float) -> str:
    """ حساب المسافة والتوصيل من إحداثيات المحل الجغرافي مع حماية صارمة ضد البيانات الفارغة """
    try:
        m_lat = merchant_data.get('latitude')
        m_lng = merchant_data.get('longitude')
        
        # حماية من الـ Crash الصامت إذا لم يقم التاجر بضبط موقعه بعد
        if not m_lat or not m_lng:
            return "تم تفعيل خدمة التوصيل في متجرنا، ولكن لم يتم ضبط إحداثيات المحل الجغرافية في النظام بعد لحساب السعر الفعلي لموقعك."
            
        merchant_coords = (float(m_lat), float(m_lng))
        distance_km = geodesic(merchant_coords, (customer_lat, customer_lng)).kilometers
        fee_per_km = merchant_data.get('delivery_fee_per_km', 0)
        total_cost = distance_km * float(fee_per_km if fee_per_km else 0)
        
        return f"المسافة التقريبية للمحل هي {round(distance_km, 2)} كم، وتكلفة التوصيل الإجمالية لموقعك هي: {round(total_cost, 2)} دينار."
    except Exception as e: 
        return f"خطأ أثناء حساب تكلفة التوصيل الجغرافي: {str(e)}"
# ----------------------------------------------------
# عقل البوت الأساسي والتكامل الصارم مع Gemini
# ----------------------------------------------------
def process_message_with_gemini(sender_id, user_message, merchant_data, image_bytes_data=None):
    try:
        merchant_id = merchant_data.get('id')
        merchant_name = merchant_data.get('business_name', 'المحل')
        merchant_phone = merchant_data.get('phone_number', 'غير مسجل')
        business_type = merchant_data.get('business_type', 'خدماتي')
        provides_delivery = merchant_data.get('provides_delivery', False)

        # سحب المنتجات حياً من قاعدة البيانات للتاجر الحالي بناءً على الـ UUID
        try:
            live_products = supabase.table("products").select("*").eq("merchant_id", merchant_id).execute().data
        except:
            live_products = []
        products_context_str = json.dumps(live_products if live_products else [], ensure_ascii=False)

        history_contents = get_chat_context(sender_id, merchant_id)
        user_parts = []
        
        if image_bytes_data:
            user_parts.append(types.Part.from_bytes(data=image_bytes_data, mime_type="image/jpeg"))
            user_parts.append(types.Part.from_text(text="افحص صورة هذا الإعلان وتعرف على السلعة وسعرها من القائمة المتوفرة."))
        
        if user_message:
            user_parts.append(types.Part.from_text(text=str(user_message)))
            
        history_contents.append(types.Content(role="user", parts=user_parts))
        
        if provides_delivery:
            delivery_instruction = (
                "إذا سألك الزبون عن التوصيل أو سعر التوصيل، أجب حصرياً ومباشرة بالعبارة التالية دون تعديل حرف واحد: "
                "(نعم، التوصيل متوفر لدينا. يرجى مشاركة موقعك الدقيق من خلال الضغط على زر مشاركة الموقع لتصلنا إحداثياتك تلقائياً وحساب التكلفة.)"
            )
        else:
            delivery_instruction = "نحن لا نوفر خدمة التوصيل حالياً. اعتذر للزبون بلطف بسطر واحد وأخبره أن الخدمة غير متوفرة."

        system_instruction = (
            f"أنت المساعد الافتراضي الآلي لصفحة ({merchant_name}) المتخصصة في ({business_type}). "
            f"معلومات التواصل الوحيدة الخاصة بنا هي: رقم الهاتف هو ({merchant_phone}). "
            f"إليك قائمة المنتجات الحية المتوفرة بالأسعار والصور: ({products_context_str}). "
            "التزم بهذه القائمة حرفياً لتقديم الأسعار. إذا سأل الزبون عن منتج متوفر فيها (مثل البرغر)، أجب بسعره وتفاصيله فوراً. "
            "إذا طلب الزبون قائمة السلع بالكامل أو المنيو، يجب أن تجيبه بعبارة: (إليك قائمة المنتجات الخاصة بنا:) فقط وممنوع زيادة أي كلام بعدها. "
            "إذا كان يستفسر عن منتج واحد محدد بالاسم، اكتب في نهاية رسالتك عبارة: (إليك كارت السلعة المعنية:) ليعرض النظام كارت السلعة المصور. "
            f"{delivery_instruction} "
            "أجب باختصار شديد جداً (سطر واحد فقط للرد)، بأسلوب محترف، وممنوع ذكر جوجل أو منصة SaaS نهائياً."
        )
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,  # دقة حديدية لمنع الهلوسة وتكرار العبارات
            max_output_tokens=200
        )
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=history_contents,
            config=config
        )
        return response.text if response.text else "مرحباً بك، كيف يمكنني مساعدتك اليوم؟"
    except Exception as e:
        print("خطأ في معالجة Gemini:", e)
        return "معذرةً، حدث خطأ مؤقت في نظام المعالجة الذكي."

# ----------------------------------------------------
# دوال فيسبوك الرسومية وإرسال المرفقات والأزرار
# ----------------------------------------------------
def send_facebook_action_dynamic(recipient_id, access_token, action_type="typing_on"):
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    try:
        requests.post(url, json={"recipient": {"id": recipient_id}, "sender_action": action_type}, params={"access_token": access_token})
    except Exception as e: print("خطأ إشارة فيسبوك:", e)

def send_location_button_dynamic(recipient_id, access_token, text_message):
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text_message, "quick_replies": [{"content_type": "location"}]}
    }
    try: requests.post(url, json=payload, params={"access_token": access_token})
    except Exception as e: print("خطأ زر الموقع:", e)

def send_dynamic_products_carousel(recipient_id, access_token, merchant_id):
    try:
        products_list = supabase.table("products").select("*").eq("merchant_id", merchant_id).limit(10).execute().data
        if not products_list: return
            
        elements = []
        for prod in products_list:
            elements.append({
                "title": str(prod.get('title', 'سلعة متوفرة')),
                "image_url": str(prod.get('image_url', '')),
                "subtitle": f"السعر: {prod.get('price', 0)} دينار | {prod.get('subtitle', '')}",
                "buttons": [{"type": "postback", "title": "🛒 طلب السلعة", "payload": f"BUY_PRODUCT_{prod.get('id')}"}]
            })
            
        url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
        payload = {"recipient": {"id": recipient_id}, "message": {"attachment": {"type": "template", "payload": {"template_type": "generic", "elements": elements}}}}
        requests.post(url, json=payload, params={"access_token": access_token})
    except Exception as e: print("خطأ في المنيو المصور:", e)

def send_single_product_card(recipient_id, access_token, product_title, image_url, price, subtitle, product_id):
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"attachment": {"type": "template", "payload": {"template_type": "generic", "elements": [{
            "title": str(product_title), "image_url": str(image_url), "subtitle": f"السعر: {price} دينار | {subtitle}",
            "buttons": [{"type": "postback", "title": "🛒 اطلبها الآن", "payload": f"BUY_PRODUCT_{product_id}"}]
        }]}}}
    }
    try: requests.post(url, json=payload, params={"access_token": access_token})
    except Exception as e: print("خطأ كارت السلعة:", e)

def send_messenger_reply_dynamic(recipient_id, text_reply, access_token):
    url = "https" + "://" + "graph" + "." + "facebook" + "." + "com" + "/v21.0" + "/me" + "/messages"
    try: requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text_reply}}, params={"access_token": access_token})
    except Exception as e: print("خطأ رد الـ API:", e)
# ----------------------------------------------------
# دالة معالجة الرسائل والويب هوك متعدد الوسائط الآمن
# ----------------------------------------------------
def background_message_processor(messaging, merchant_data):
    try:
        sender_id = messaging['sender']['id']
        merchant_id = merchant_data['id']
        
        encrypted_token = merchant_data.get('page_access_token')
        merchant_token = decrypt_token(encrypted_token) if encrypted_token else PAGE_ACCESS_TOKEN

        send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_on")

        image_bytes_data = None
        message_text = ""

        if 'message' in messaging and 'attachments' in messaging['message']:
            for attachment in messaging['message']['attachments']:
                if attachment.get('type') == 'location':
                    coordinates = attachment['payload']['coordinates']
                    save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل موقعه الجغرافي]")
                    reply_text = calculate_delivery_cost_dynamic(merchant_data, coordinates['lat'], coordinates['long'])
                    save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
                    send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
                    send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                    return
                
                elif attachment.get('type') == 'image':
                    try:
                        img_response = requests.get(attachment['payload']['url'])
                        if img_response.status_code == 200:
                            image_bytes_data = img_response.content
                            save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text="[أرسل صورة منتج]")
                    except: pass

        if 'message' in messaging and 'text' in messaging['message']:
            message_text = messaging['message']['text']
            if not image_bytes_data:
                save_to_chat_history_dynamic(merchant_id, sender=sender_id, message_text=message_text)

        if message_text or image_bytes_data:
            reply_text = process_message_with_gemini(sender_id, message_text, merchant_data, image_bytes_data)
            save_to_chat_history_dynamic(merchant_id, sender="bot", message_text=reply_text)
            send_facebook_action_dynamic(sender_id, merchant_token, action_type="typing_off")
            
            # [الالتقاط الذكي والمضمون للسيناريوهات دون تكرار]
            if "نعم، التوصيل متوفر لدينا" in reply_text or "مشاركة موقعك الدقيق" in reply_text:
                send_location_button_dynamic(sender_id, merchant_token, reply_text)
            elif "إليك قائمة المنتجات الخاصة بنا" in reply_text or "المنيو" in reply_text:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                send_dynamic_products_carousel(sender_id, merchant_token, merchant_id)
            elif "إليك كارت السلعة المعنية" in reply_text:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                live_prods = supabase.table("products").select("*").eq("merchant_id", merchant_id).execute().data
                if live_prods:
                    for p in live_prods:
                        if str(p.get('title')) in reply_text or (message_text and str(p.get('title')) in message_text):
                            send_single_product_card(sender_id, merchant_token, p.get('title'), p.get('image_url'), p.get('price'), p.get('subtitle'), p.get('id'))
                            break
            else:
                send_messenger_reply_dynamic(sender_id, reply_text, merchant_token)
                
    except Exception as e:
        print("خطأ حرج في الخلفية:", e)

@app.route('/', methods=['GET'])
def home(): return "SaaS Server Alive 🚀", 200

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return request.args.get('hub.challenge'), 200
    return "خطأ توكن", 403

@app.route('/webhook', methods=['POST'])
def handle_messages():
    """ استقبال الرسائل وتدمير التكرار حياً عبر فحص الـ message_id الفريد ومطابقة التاجر بأمان """
    data = request.get_json()
    if data and data.get('object') in ['page', 'instagram']:
        for entry in data.get('entry', []):
            for messaging_event in entry.get('messaging', []):
                m_id = messaging_event.get('message', {}).get('mid')
                if m_id:
                    if m_id in PROCESSED_MESSAGE_IDS: 
                        return "OK", 200
                    PROCESSED_MESSAGE_IDS.add(m_id)

                f_page_id = messaging_event['recipient']['id']
                
                # جلب وتصفية بيانات التجار من قاعدة البيانات
                m_query = supabase.table("merchants").select("*").execute()
                merchant_data = None
                
                # التصحيح الصارم والنهائي لاسم المتغير لتفادي خطأ NameError
                if m_query.data:
                    for merchant in m_query.data:
                        if str(merchant.get('facebook_page_id', '')).strip() == str(f_page_id).strip():
                            merchant_data = merchant
                            break
                            
                if merchant_data:
                    threading.Thread(target=background_message_processor, args=(messaging_event, merchant_data)).start()
                    
        return "OK", 200
    return "طلب غير صالح", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
