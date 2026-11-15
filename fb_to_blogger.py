"""
بوت نقل العروض العقارية من صفحة فيسبوك إلى مدونة Blogger عبر Gemini.

قبل التشغيل، عرّف متغيرات البيئة التالية (لا تكتب أي مفتاح داخل الكود):

  أساسية:
    GEMINI_API_KEY          مفتاح Gemini API (من Google AI Studio)
    BLOGGER_BLOG_ID         معرف المدونة الرقمي
    FB_USER_ACCESS_TOKEN    توكن مستخدم فيسبوك

  اختيارية:
    FB_PAGE_NAME            اسم صفحة فيسبوك (افتراضي: WassataAkariaTijaria)
    WHATSAPP_NUMBER         رقم واتساب للتواصل (افتراضي: 212708565088)

  لتجنّب تجديد توكن بلوجر يدوياً في كل تشغيل (موصى به لتشغيل دوري/cron)،
  عرّف الثلاثة التالية معاً، وسيجدد السكريبت access token تلقائياً من refresh
  token في كل مرة:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REFRESH_TOKEN
  شغّل get_google_refresh_token.py المرفق مرة واحدة على جهازك للحصول عليها
  (يحتاج متصفحاً، فلا يمكن تنفيذه من هنا).

  لتجنّب انتهاء صلاحية توكن المستخدم في فيسبوك (عادة ساعة إلى ساعتين)، عرّف
  أيضاً:
    FB_APP_ID
    FB_APP_SECRET
  (تجدهما في لوحة تطبيقك على developers.facebook.com). عند توفرهما، يستبدل
  السكريبت FB_USER_ACCESS_TOKEN تلقائياً بتوكن طويل الأمد (~60 يوماً) في بداية
  كل تشغيل، وتوكن الصفحة الناتج عنه عملياً لا ينتهي ما دمت لم تغيّر كلمة مرور
  حسابك أو تُلغِ صلاحية التطبيق.

  بدون هذين المتغيرين الإضافيين (GOOGLE_* وFB_APP_*) سيعمل السكريبت كما كان:
  تحتاج إلى لصق BLOGGER_ACCESS_TOKEN وFB_USER_ACCESS_TOKEN يدوياً، وتجديدهما
  كلما انتهت صلاحيتهما.
    BLOGGER_ACCESS_TOKEN    (مطلوب فقط إن لم تُعرّف GOOGLE_REFRESH_TOKEN)

مثال تشغيل (لينكس/ماك):
    export GEMINI_API_KEY="..."
    export BLOGGER_BLOG_ID="..."
    export FB_USER_ACCESS_TOKEN="..."
    export GOOGLE_CLIENT_ID="..."
    export GOOGLE_CLIENT_SECRET="..."
    export GOOGLE_REFRESH_TOKEN="..."
    export FB_APP_ID="..."
    export FB_APP_SECRET="..."
    python3 fb_to_blogger.py

يعتمد هذا الملف على مكتبة Gemini الجديدة "google-genai" (المكتبة القديمة
"google-generativeai" توقفت نهائياً في 30 نونبر 2025، كما تم سحب الموديل
"gemini-1.5-pro" من الـ API في 24 شتنبر 2025). ثبّتها عبر:
    pip install google-genai
"""

import json
import os
import sqlite3
import sys
import time
from urllib.parse import quote

import requests
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# الإعدادات: تُقرأ من متغيرات البيئة فقط، لا مفاتيح مكتوبة هنا أبداً
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID")
BLOGGER_ACCESS_TOKEN = os.environ.get("BLOGGER_ACCESS_TOKEN")  # fallback إن لم يوجد refresh token

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_AUTO_REFRESH = bool(
    GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN
)

FB_PAGE_NAME = os.environ.get("FB_PAGE_NAME", "WassataAkariaTijaria")
FB_USER_ACCESS_TOKEN = os.environ.get("FB_USER_ACCESS_TOKEN")
FB_API_VERSION = os.environ.get("FB_API_VERSION", "v21.0")
FB_APP_ID = os.environ.get("FB_APP_ID")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET")
FB_AUTO_EXTEND = bool(FB_APP_ID and FB_APP_SECRET)

WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "212708565088")
DB_NAME = os.environ.get("DB_NAME", "published_posts.db")

REQUIRED_VARS = {
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "BLOGGER_BLOG_ID": BLOGGER_BLOG_ID,
    "FB_USER_ACCESS_TOKEN": FB_USER_ACCESS_TOKEN,
}


def check_required_env():
    """تتأكد من وجود كل متغيرات البيئة الضرورية قبل بدء التنفيذ."""
    missing = [name for name, value in REQUIRED_VARS.items() if not value]
    if not GOOGLE_AUTO_REFRESH and not BLOGGER_ACCESS_TOKEN:
        missing.append("BLOGGER_ACCESS_TOKEN (أو GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN)")
    if missing:
        print("متغيرات البيئة التالية غير معرّفة: " + ", ".join(missing))
        print("عرّفها قبل التشغيل، راجع التعليق أعلى الملف لمعرفة الطريقة.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# قاعدة البيانات المحلية (SQLite) - اتصال واحد يُغلق بشكل صريح
# ---------------------------------------------------------------------------
def get_connection():
    return sqlite3.connect(DB_NAME)


def init_db():
    """تنشئ قاعدة البيانات المحلية وجدول المنشورات المنشورة مسبقاً."""
    conn = get_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS posted_items (
                post_id TEXT PRIMARY KEY,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def is_post_published(post_id):
    """تتحقق مما إذا كان المنشور قد تم نشره من قبل."""
    conn = get_connection()
    try:
        return (
            conn.execute(
                "SELECT 1 FROM posted_items WHERE post_id = ?", (post_id,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def mark_post_as_published(post_id):
    """تسجل معرف المنشور في قاعدة البيانات بعد نجاح نشره."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO posted_items (post_id) VALUES (?)", (post_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# تجديد توكن Google (Blogger) تلقائياً عبر refresh token
# ---------------------------------------------------------------------------
def refresh_blogger_access_token():
    """تستبدل BLOGGER_ACCESS_TOKEN بتوكن جديد عبر refresh token، إن كانت
    GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN معرّفة. تُحدّث المتغير العام مباشرة
    وتُعيد True عند النجاح."""
    global BLOGGER_ACCESS_TOKEN

    if not GOOGLE_AUTO_REFRESH:
        return BLOGGER_ACCESS_TOKEN is not None

    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    try:
        response = requests.post(url, data=payload, timeout=30)
        data = response.json()
        if response.status_code != 200 or "access_token" not in data:
            print(f"فشل تجديد توكن Google: {data}")
            return False
        BLOGGER_ACCESS_TOKEN = data["access_token"]
        print("تم تجديد توكن Google/Blogger تلقائياً.")
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"خطأ اتصال أثناء تجديد توكن Google: {exc}")
        return False


# ---------------------------------------------------------------------------
# فيسبوك
# ---------------------------------------------------------------------------
def extend_facebook_user_token():
    """تستبدل FB_USER_ACCESS_TOKEN بتوكن طويل الأمد (~60 يوماً)، إن كانت
    FB_APP_ID وFB_APP_SECRET معرّفتين. توكن الصفحة الناتج عنه لاحقاً سيكون
    عملياً دائم الصلاحية."""
    global FB_USER_ACCESS_TOKEN

    if not FB_AUTO_EXTEND:
        return

    url = f"https://graph.facebook.com/{FB_API_VERSION}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": FB_APP_ID,
        "client_secret": FB_APP_SECRET,
        "fb_exchange_token": FB_USER_ACCESS_TOKEN,
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        data = response.json()
        if response.status_code != 200 or "access_token" not in data:
            print(f"فشل تمديد توكن فيسبوك، سيُستخدم التوكن الأصلي: {data}")
            return
        FB_USER_ACCESS_TOKEN = data["access_token"]
        print("تم تمديد توكن فيسبوك إلى توكن طويل الأمد.")
    except (requests.RequestException, ValueError) as exc:
        print(f"خطأ اتصال أثناء تمديد توكن فيسبوك: {exc}")


def resolve_facebook_page_token():
    """تستخرج المعرف الرقمي للصفحة وتوكن الصفحة الخاص بها تلقائياً."""
    url = f"https://graph.facebook.com/{FB_API_VERSION}/me/accounts"
    params = {
        "fields": "id,name,username,access_token",
        "access_token": FB_USER_ACCESS_TOKEN,
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        data = response.json()
        if response.status_code != 200:
            print(f"خطأ فيسبوك عند جلب الصفحات: {data}")
            return None, None

        pages = data.get("data", [])
        if not pages:
            print("لم يتم العثور على أي صفحات مرتبطة بهذا الحساب.")
            return None, None

        for page in pages:
            if (
                FB_PAGE_NAME.lower() in page.get("name", "").lower()
                or FB_PAGE_NAME.lower() == page.get("username", "").lower()
            ):
                print(f"تم التعرف على الصفحة: {page.get('name')} (ID: {page.get('id')})")
                return page["id"], page["access_token"]

        first_page = pages[0]
        print(f"تم اختيار الصفحة المتاحة: {first_page.get('name')} (ID: {first_page.get('id')})")
        return first_page["id"], first_page["access_token"]
    except (requests.RequestException, ValueError) as exc:
        print(f"خطأ اتصال أثناء جلب بيانات الصفحة: {exc}")
        return None, None


def fetch_latest_facebook_posts(page_id, page_access_token):
    """تسحب أحدث المنشورات والصور من صفحة فيسبوك."""
    url = f"https://graph.facebook.com/{FB_API_VERSION}/{page_id}/posts"
    params = {
        "fields": "id,message,created_time,full_picture",
        "access_token": page_access_token,
        "limit": 10,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        data = response.json()
        if response.status_code != 200:
            print(f"خطأ فيسبوك أثناء جلب المنشورات: {data}")
            return []
        return data.get("data", [])
    except (requests.RequestException, ValueError) as exc:
        print(f"حدث خطأ أثناء جلب المنشورات من Facebook: {exc}")
        return []


# ---------------------------------------------------------------------------
# Gemini: استخراج بيانات العقار
# ---------------------------------------------------------------------------
_gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def extract_property_strict_json(post_text, image_url="", max_retries=3):
    """تستخرج تفاصيل العقار بصيغة JSON باستخدام Gemini، ثم تُدرج وسم الصورة
    برمجياً (بدل الاعتماد على التزام الموديل بتضمينه حرفياً)."""

    prompt = f"""
أنت وكيل ذكاء اصطناعي محترف لمنصة الوساطة العقارية والتجارية.
حلل نص منشور فيسبوك التالي واستخرج تفاصيل العقار باللغة العربية.
أعد JSON صحيحاً فقط وبالمفتاحين التاليين:
{{
  "title": "عنوان جذاب ومحترف للعقار يتضمن نوعه والمنطقة",
  "html_content": "مقال احترافي كامل بصيغة HTML يصف العقار ومواصفاته وسعره، بدون أي وسم img"
}}

نص المنشور:
{post_text}
"""

    for attempt in range(1, max_retries + 1):
        try:
            response = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            if not isinstance(data, dict) or not all(
                isinstance(data.get(key), str) and data[key].strip()
                for key in ("title", "html_content")
            ):
                raise ValueError("استجابة Gemini لا تحتوي على الحقول المطلوبة.")

            html_content = data["html_content"]
            if image_url:
                image_tag = (
                    f"<img src='{image_url}' alt='عقار' "
                    f"style='max-width:100%; height:auto; border-radius:8px;'/><br>"
                )
                html_content = image_tag + html_content

            whatsapp_text = quote("السلام عليكم، مهتم بالعقار المذكور")
            contact_html = (
                "<p><strong>للتواصل والاستفسار:</strong> "
                f"<a href='https://wa.me/{WHATSAPP_NUMBER}?text={whatsapp_text}' "
                "target='_blank'>تواصل عبر واتساب</a></p>"
            )
            data["html_content"] = html_content + contact_html
            return data

        except Exception as exc:
            print(f"محاولة {attempt}/{max_retries} فشلت في استخراج JSON عبر الذكاء الاصطناعي: {exc}")
            if attempt < max_retries:
                time.sleep(2 * attempt)

    return None


# ---------------------------------------------------------------------------
# Blogger
# ---------------------------------------------------------------------------
def _post_to_blogger_once(title, html_content):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts/"
    headers = {
        "Authorization": f"Bearer {BLOGGER_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "kind": "blogger#post",
        "blog": {"id": BLOGGER_BLOG_ID},
        "title": title,
        "content": html_content,
    }
    return requests.post(url, headers=headers, json=payload, timeout=30)


def publish_to_blogger(title, html_content):
    """تنشر المقال في مدونة بلوجر عبر API v3 بصيغة JSON. تحاول تجديد التوكن
    تلقائياً مرة واحدة إذا رفضه بلوجر بخطأ 401 (ويتوفر GOOGLE_REFRESH_TOKEN)."""
    try:
        response = _post_to_blogger_once(title, html_content)

        if response.status_code == 401 and GOOGLE_AUTO_REFRESH:
            print("انتهت صلاحية توكن بلوجر، جاري تجديده تلقائياً وإعادة المحاولة...")
            if refresh_blogger_access_token():
                response = _post_to_blogger_once(title, html_content)

        if response.status_code in (200, 201):
            return True
        if response.status_code == 401:
            print(
                "فشل النشر على بلوجر: 401 غير مصرح به - انتهت صلاحية "
                "BLOGGER_ACCESS_TOKEN. جدّده يدوياً، أو أضف GOOGLE_CLIENT_ID/"
                "SECRET/REFRESH_TOKEN للتجديد التلقائي."
            )
        else:
            print(f"فشل النشر على بلوجر ({response.status_code}): {response.text}")
    except requests.RequestException as exc:
        print(f"حدث خطأ أثناء النشر على بلوجر: {exc}")
    return False


# ---------------------------------------------------------------------------
# التنفيذ الرئيسي
# ---------------------------------------------------------------------------
def main():
    check_required_env()
    init_db()

    if GOOGLE_AUTO_REFRESH:
        if not refresh_blogger_access_token():
            print("تعذر الحصول على توكن بلوجر عبر refresh token. راجع الإعدادات.")
            return

    extend_facebook_user_token()

    print("جاري التحقق من صلاحيات الصفحة واستخراج التوكن...")
    page_id, page_token = resolve_facebook_page_token()

    if not page_id or not page_token:
        print("تعذر الوصول إلى صفحة فيسبوك. راجع رسالة الخطأ أعلاه.")
        return

    print("جاري فحص صفحة فيسبوك بحثاً عن عروض عقارية جديدة...")
    posts = fetch_latest_facebook_posts(page_id, page_token)

    if not posts:
        print("لا توجد منشورات متاحة في الوقت الحالي.")
        return

    new_posts_count = 0
    for post in posts:
        post_id = post.get("id")
        post_message = post.get("message", "")
        post_image = post.get("full_picture", "")

        if not post_id or is_post_published(post_id) or not post_message:
            continue

        print(f"\nتم العثور على منشور جديد (ID: {post_id}). جاري معالجته...")
        property_data = extract_property_strict_json(post_message, post_image)
        if property_data and publish_to_blogger(
            property_data["title"], property_data["html_content"]
        ):
            mark_post_as_published(post_id)
            print(f"تم نشر العقار بنجاح وحفظه في قاعدة البيانات (ID: {post_id})")
            new_posts_count += 1

        # تهدئة بسيطة بين الطلبات لتفادي حدود معدل الاستخدام (rate limit)
        time.sleep(1.5)

    print(f"\nانتهت الدورة. تم نشر {new_posts_count} عقاراً جديداً في المدونة.")


if __name__ == "__main__":
    main()
