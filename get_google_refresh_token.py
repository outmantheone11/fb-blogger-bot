"""
أداة تُستخدم مرة واحدة فقط للحصول على GOOGLE_REFRESH_TOKEN اللازم لتجديد
توكن بلوجر تلقائياً في fb_to_blogger.py.

تحتاج هذه الأداة متصفحاً، لذا شغّلها على جهازك الشخصي (وليس على خادم بلا واجهة
رسومية).

الخطوات:
1) في Google Cloud Console (console.cloud.google.com):
   - أنشئ مشروعاً (أو استخدم مشروعاً موجوداً).
   - فعّل "Blogger API v3".
   - من "OAuth consent screen" اختر External وأكمل الإعداد البسيط (أضف
     بريدك كمستخدم اختبار إن طُلب ذلك).
   - من "Credentials" أنشئ "OAuth client ID" من نوع "Desktop app"، وستحصل
     على Client ID وClient Secret.

2) عرّف متغيرين في الطرفية قبل التشغيل:
       export GOOGLE_CLIENT_ID="..."
       export GOOGLE_CLIENT_SECRET="..."

3) شغّل:
       pip install requests
       python3 get_google_refresh_token.py

   سيفتح رابطاً تنسخه وتفتحه في المتصفح، تسجّل دخولك وتوافق على الصلاحية،
   ثم تحصل على "رمز" (code) تلصقه هنا عند الطلب.

4) ستحصل في الأخير على GOOGLE_REFRESH_TOKEN، احتفظ به في مكان آمن
   (لا تكتبه داخل الكود، استعمله كمتغير بيئة).
"""

import os
import sys
import urllib.parse

import requests

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
SCOPE = "https://www.googleapis.com/auth/blogger"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"  # تدفق "نسخ/لصق" المناسب للأدوات بلا متصفح مدمج


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("عرّف GOOGLE_CLIENT_ID وGOOGLE_CLIENT_SECRET أولاً (راجع التعليق أعلى الملف).")
        sys.exit(1)

    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)

    print("افتح هذا الرابط في متصفحك ووافق على الصلاحية:\n")
    print(auth_url)
    print()
    code = input("ألصق هنا الرمز (code) الذي حصلت عليه بعد الموافقة: ").strip()

    token_response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    data = token_response.json()

    if "refresh_token" not in data:
        print("لم يتم الحصول على refresh_token. الرد الكامل:")
        print(data)
        print(
            "\nملاحظة شائعة: إن كنت قد ربطت هذا الحساب بالتطبيق من قبل، أعد "
            "المحاولة بعد إلغاء صلاحية الوصول من myaccount.google.com/permissions "
            "ثم إعادة التشغيل، حتى تحصل Google على موافقة جديدة تتضمن refresh_token."
        )
        sys.exit(1)

    print("\nتم بنجاح! استخدم هذه القيم كمتغيرات بيئة في fb_to_blogger.py:\n")
    print(f'GOOGLE_CLIENT_ID="{CLIENT_ID}"')
    print(f'GOOGLE_CLIENT_SECRET="{CLIENT_SECRET}"')
    print(f'GOOGLE_REFRESH_TOKEN="{data["refresh_token"]}"')


if __name__ == "__main__":
    main()
