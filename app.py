from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from google import genai
from urllib.parse import quote_plus
import os
import requests
import time
import random
from datetime import datetime, timedelta



load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = Flask(__name__)
app.secret_key = "ai-code-explainer-secret-key"

postgres_password = quote_plus(os.getenv("POSTGRES_PASSWORD"))

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"postgresql+psycopg2://"
    f"{os.getenv('POSTGRES_USER')}:{postgres_password}@"
    f"{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/"
    f"{os.getenv('POSTGRES_DB')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    otp = db.Column(db.String(6), nullable=True)
    otp_expiry = db.Column(db.DateTime, nullable=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)

class CodeHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    language = db.Column(db.String(50), nullable=False)
    code = db.Column(db.Text, nullable=False)
    result = db.Column(db.Text, nullable=False)
    result_type = db.Column(db.String(20), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user = db.relationship("User", backref="code_histories")
    created_at = db.Column(db.DateTime, default=db.func.now())
   

with app.app_context():
    db.create_all()

def analyze_code_complexity(code):
    lines = code.splitlines()

    total_lines = len(lines)
    blank_lines = sum(1 for line in lines if not line.strip())
    comment_lines = sum(
        1 for line in lines
        if line.strip().startswith(("#", "//", "/*", "*"))
    )

    code_lines = total_lines - blank_lines - comment_lines

    return {
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines
    }

def generate_otp():
    return str(random.randint(100000, 999999))

def send_otp_email(email, otp):
    api_key = os.getenv("BREVO_API_KEY")

    url = "https://api.brevo.com/v3/smtp/email"

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    payload = {
        "sender": {
            "name": "AI Code Explainer",
            "email": os.getenv("MAIL_USERNAME")
        },
        "to": [
            {"email": email}
        ],
        "subject": "Your AI Code Explainer OTP",
        "textContent": f"""
Hello,

Your OTP for AI Code Explainer is: {otp}

Please do not share this OTP with anyone.

Regards,
AI Code Explainer System
"""
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    if not response.ok:
        print("Brevo Error:", response.status_code, response.text)

    response.raise_for_status()


def send_welcome_email(email, name):
    api_key = os.getenv("BREVO_API_KEY")

    url = "https://api.brevo.com/v3/smtp/email"

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    payload = {
        "sender": {
            "name": "AI Code Explainer",
            "email": os.getenv("MAIL_USERNAME")
        },
        "to": [
            {"email": email, "name": name}
        ],
        "subject": "Welcome to AI Code Explainer",
        "textContent": f"""
Hello {name},

Welcome to AI Code Explainer!

Your account has been created successfully.

You can now:
- Explain programming code
- Detect coding errors
- View your code history
- Improve your code

Thank you for joining us!

Regards,
AI Code Explainer System
"""
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    if not response.ok:
        print("Welcome Email Error:", response.status_code, response.text)

    response.raise_for_status()



@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
         return render_template(
        "register.html",
        error="Passwords do not match."
         )

        existing_user = User.query.filter_by(email=email).first()

        if existing_user:

            if existing_user.email_verified:
                return render_template(
                    "register.html",
                    error="Email already exists. Please use another email address."
                )

            # Existing account is not verified
            new_otp = generate_otp()

            existing_user.name = name
            existing_user.password = generate_password_hash(password)
            existing_user.otp = new_otp
            existing_user.otp_expiry = datetime.now() + timedelta(minutes=5)

            db.session.commit()

            send_otp_email(email, new_otp)

            session["pending_user_id"] = existing_user.id

            return redirect(url_for("verify_otp"))

        hashed_password = generate_password_hash(password)

        otp = generate_otp()

        new_user = User(
            name=name,
            email=email,
            password=hashed_password,
            otp=otp,
            otp_expiry=datetime.now() + timedelta(minutes=5)
        )

        db.session.add(new_user)
        db.session.commit()

        send_otp_email(email, otp)
        send_welcome_email(email, name)

        session["pending_user_id"] = new_user.id

        return redirect(url_for("verify_otp"))

    return render_template("register.html")

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    user_id = session.get("pending_user_id")

    if not user_id:
        return redirect(url_for("register"))

    user = User.query.get(user_id)

    if request.method == "POST":
        entered_otp = request.form["otp"]

        if not user:
            return render_template(
                "verify_otp.html",
                error="User not found."
            )

        if not user.otp:
            return render_template(
                "verify_otp.html",
                error="OTP not found."
            )

        if datetime.now() > user.otp_expiry:
            return render_template(
                "verify_otp.html",
                error="OTP has expired. Please register again."
            )

        if entered_otp != user.otp:
            return render_template(
                "verify_otp.html",
                error="Invalid OTP. Please try again."
            )

        user.email_verified = True
        user.otp = None
        user.otp_expiry = None

        db.session.commit()

        session.pop("pending_user_id", None)

        return redirect(url_for("home"))

    return render_template("verify_otp.html")

@app.route("/resend-otp")
def resend_otp():
    user_id = session.get("pending_user_id")

    if not user_id:
        return redirect(url_for("register"))

    user = User.query.get(user_id)

    if not user:
        return redirect(url_for("register"))

    new_otp = generate_otp()

    user.otp = new_otp
    user.otp_expiry = datetime.now() + timedelta(minutes=5)

    db.session.commit()

    send_otp_email(user.email, new_otp)

    return render_template(
        "verify_otp.html",
        message="A new OTP has been sent to your email."
    )

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET":
        return render_template("login.html")
   
    email = request.form["email"]
    password = request.form["password"]

    user = User.query.filter_by(email=email).first()

    if not user:
        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    if not check_password_hash(user.password, password):
        return render_template(
            "login.html",
            error="Invalid email or password."
        )

    if not user.email_verified:
        return render_template(
            "login.html",
            error="Please verify your email before logging in."
        )

    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_role"] = user.role

    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form["email"]
        user = User.query.filter_by(email=email).first()

        if user:

            otp = generate_otp()
            user.otp = otp
            user.otp_expiry = datetime.now() + timedelta(minutes=5)

            send_otp_email(user.email, otp)

            db.session.commit()

            session["forgot_password_user_id"] = user.id

            return redirect(url_for("verify_forgot_password_otp"))

        else:
            return "Email not registered."

    return render_template("forgot_password.html")

@app.route("/verify-forgot-password-otp", methods=["GET", "POST"])
def verify_forgot_password_otp():

    user_id = session.get("forgot_password_user_id")

    if not user_id:
        return redirect(url_for("forgot_password"))

    user = User.query.get(user_id)

    if not user:
        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        entered_otp = request.form["otp"]

        if not user.otp:
            return render_template(
                "forgot_password_otp.html",
                error="OTP not found. Please request a new OTP."
            )

        if datetime.now() > user.otp_expiry:
            return render_template(
                "forgot_password_otp.html",
                error="OTP has expired. Please request a new OTP."
            )

        if entered_otp != user.otp:
            return render_template(
                "forgot_password_otp.html",
                error="Invalid OTP. Please try again."
            )

        session["forgot_password_verified"] = True

        user.otp = None
        user.otp_expiry = None

        db.session.commit()

        return redirect(url_for("reset_password"))

    return render_template("forgot_password_otp.html")

@app.route("/resend-forgot-password-otp")
def resend_forgot_password_otp():

    email = session.get("forgot_password_email")

    if not email:
        return redirect(url_for("forgot_password"))

    user = User.query.filter_by(email=email).first()

    if not user:
        return redirect(url_for("forgot_password"))

    otp = generate_otp()

    user.otp = otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=5)

    db.session.commit()

    send_otp_email(email, otp)

    return redirect(url_for("verify_forgot_password_otp"))

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():

    if not session.get("forgot_password_verified"):
        return redirect(url_for("forgot_password"))

    user_id = session.get("forgot_password_user_id")

    if not user_id:
        return redirect(url_for("forgot_password"))

    user = User.query.get(user_id)

    if not user:
        return redirect(url_for("forgot_password"))

    if request.method == "POST":

        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        if new_password != confirm_password:
            return render_template(
                "reset_password.html",
                error="Passwords do not match."
            )

        user.password = generate_password_hash(new_password)

        db.session.commit()

        session.pop("forgot_password_verified", None)
        session.pop("forgot_password_user_id", None)

        return render_template(
            "login.html",
            success="Password updated successfully. Please login with your new password."
        )
    return render_template("reset_password.html")


@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("home"))

    return render_template("dashboard.html")

@app.route("/profile")
def profile():

    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("home"))

    user = User.query.get(user_id)

    return render_template(
        "profile.html",
        user=user
    )

@app.route("/change-password", methods=["GET", "POST"])
def change_password():

    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("home"))

    user = User.query.get(user_id)

    if request.method == "POST":

        current_password = request.form["current_password"]
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        if not check_password_hash(user.password, current_password):
            return "Current password is incorrect."

        if new_password != confirm_password:
            return "New passwords do not match."

        user.password = generate_password_hash(new_password)

        db.session.commit()

        return "Password changed successfully. You can now login with your new password."

    return render_template("change_password.html")

@app.route("/history")
def history():

    if "user_id" not in session:
        return redirect(url_for("home"))

    if session.get("user_role") == "admin":
        codes = CodeHistory.query.order_by(CodeHistory.id.desc()).all()
    else:
        codes = CodeHistory.query.filter_by(
            user_id=session.get("user_id")
        ).order_by(CodeHistory.id.desc()).all()

    return render_template("history.html", codes=codes)

@app.route("/explain", methods=["GET", "POST"])
def explain():

    if "user_id" not in session:
        return redirect(url_for("home"))

    if request.method == "POST":

        language = request.form["language"]
        code = request.form["code"]
        complexity = analyze_code_complexity(code)

        prompt = f"""
You are an expert programming teacher.

Explain the following {language} code in very simple language.

Code:
{code}

Give:

1. What the code does
2. Line-by-line explanation
3. Any errors
4. How to improve the code
5. Time Complexity
6. Space Complexity

For Time Complexity and Space Complexity:
- Give the Big-O notation.
- Give a short and simple reason.
- If complexity cannot be determined accurately, clearly say so.
"""

        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt
            )

            explanation = response.text

        except Exception as e:
            return f"Gemini Error: {e}"

        new_history = CodeHistory(
           language=language,
            code=code,
            result=explanation,
            result_type="Error Detection",
            user_id=session.get("user_id")
        )

        db.session.add(new_history)
        db.session.commit()

        return render_template(
               "explain.html",
               explanation=explanation,
             complexity=complexity
         )

    return render_template("explain.html")

@app.route("/detect-error", methods=["GET", "POST"])
def detect_error():

    if "user_id" not in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        language = request.form["language"]
        code = request.form["code"]
        complexity = analyze_code_complexity(code)

        prompt = f"""
You are an expert {language} programmer.

Analyze this code for errors.

Code:
{code}

Give the answer in simple language:
1. Error found
2. Why the error occurs
3. Corrected code
4. Short explanation
"""

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        explanation = response.text

        new_history = CodeHistory(
            language=language,
            code=code,
            result=explanation,
            user_id=session.get("user_id")
        )

        db.session.add(new_history)
        db.session.commit()

        return render_template(
           "explain.html",
           explanation=explanation,
           complexity=complexity,
            selected_language=language
        )

    return render_template("explain.html")

@app.route("/admin")
def admin_dashboard():

    if "user_id" not in session:
        return redirect(url_for("home"))

    if session.get("user_role") != "admin":
        return "Access Denied"

    users = User.query.order_by(User.id.desc()).all()
    total_users = User.query.count()
    total_admins = User.query.filter_by(role="admin").count()
    total_history = CodeHistory.query.count()

    return render_template(
    "admin_dashboard.html",
    users=users,
    total_users=total_users,
    total_admins=total_admins,
    total_history=total_history
)

@app.route("/admin/change-role/<int:user_id>", methods=["POST"])
def change_role(user_id):

    if "user_id" not in session:
        return redirect(url_for("home"))

    if session.get("user_role") != "admin":
        return "Access Denied"

    if user_id == session.get("user_id"):
     return "You cannot change your own admin role."

    user = User.query.get(user_id)

    if not user:
        return "User not found"

    if user.role == "user":
        user.role = "admin"
    else:
        user.role = "user"

    db.session.commit()

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
def delete_user(user_id):

    if "user_id" not in session:
        return redirect(url_for("home"))

    if session.get("user_role") != "admin":
        return "Access Denied"

    # Admin cannot delete their own account
    if user_id == session.get("user_id"):
        return "You cannot delete your own admin account."

    user = User.query.get(user_id)

    if not user:
        return "User not found"

    # Delete user's code history first
    CodeHistory.query.filter_by(user_id=user.id).delete()

    db.session.delete(user)
    db.session.commit()

    return redirect(url_for("admin_dashboard"))

if __name__ == "__main__":
    app.run(debug=True)
