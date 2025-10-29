from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    # Menampilkan pesan status, ini yang akan di-ping oleh UptimeRobot
    return "Bot Harga Saham Discord Aktif!"

def run():
  # Menjalankan server Flask
  # host='0.0.0.0' dan port yang sesuai dengan lingkungan Codespaces
  app.run(host='0.0.0.0', port=10000) 

def keep_alive():
    # Menjalankan server web di thread terpisah agar tidak memblokir bot Discord
    t = Thread(target=run)
    t.start()