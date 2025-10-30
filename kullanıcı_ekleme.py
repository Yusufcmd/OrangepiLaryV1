#!/usr/bin/env python3
# Kullanıcı ekleme / parola güncelleme (SQLite) - PLAINTEXT
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import getpass
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Yazılabilir bir SQLite yolu çöz (site.db kök)
try:
    import db_util
    DB_URI = db_util.resolve_sqlite_uri(BASE_DIR)
    DB_PATH = db_util.get_db_path(BASE_DIR)
except Exception:
    DB_PATH  = os.path.join(BASE_DIR, "site.db")
    DB_URI   = f"sqlite:///{DB_PATH}"

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class User(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # PLAINTEXT

def create_or_update_user(username: str, password: str):
    with app.app_context():
        db.create_all()
        u = User.query.filter_by(username=username).first()
        if u:
            u.password = password
            db.session.commit()
            print(f"Kullanıcı güncellendi: {username}")
        else:
            db.session.add(User(username=username, password=password))
            db.session.commit()
            print(f"Kullanıcı eklendi: {username}")

if __name__ == "__main__":
    # Bilgi amaçlı: hedef DB yolu
    try:
        from db_util import describe_db_target
        print("Veritabanı:", describe_db_target(BASE_DIR))
    except Exception:
        print("Veritabanı:", DB_PATH)

    username = input("Kullanıcı adı: ").strip()
    password = getpass.getpass("Şifre: ").strip()
    if not username or not password:
        raise SystemExit("Kullanıcı adı/şifre boş olamaz.")
    create_or_update_user(username, password)
