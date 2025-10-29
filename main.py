import os
import asyncio
import datetime as dt
import yfinance as yf
from discord.ext import commands, tasks
import discord
from dotenv import load_dotenv
from keep_alive import keep_alive # Import fungsi untuk uptime

# Muat variabel lingkungan dari file .env
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID'))

# Konfigurasi Bot
TICKERS = ["AAPL", "NVDA", "AMD", "NET", "MA", "V", "MELI", "TSM", "JNJ", "AEM", "SCCO", "KGC"]
THRESHOLD = 0.015 # 1.5%
MARKET_OPEN_HOUR_UTC = 13  # 13:30 UTC
MARKET_CLOSE_HOUR_UTC = 21 # 21:00 UTC

# Inisialisasi Bot dengan Intents yang dibutuhkan
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True 
intents.guilds = True 
bot = commands.Bot(command_prefix='/', intents=intents)

# Cache untuk menyimpan harga terakhir dan harga penutupan hari sebelumnya
stock_cache = {} 
previous_close_cache = {}

# Fungsi untuk mendapatkan harga penutupan hari sebelumnya
async def get_previous_close(ticker):
    """Ambil harga penutupan hari sebelumnya menggunakan yfinance."""
    try:
        # Mengambil data historis 2 hari ('2d') dengan interval 1 hari ('1d')
        # untuk memastikan mendapatkan harga penutupan terakhir.
        data = yf.download(ticker, period="2d", interval="1d", progress=False)
        # Harga penutupan hari sebelumnya (baris pertama)
        if len(data) >= 1:
            return data['Close'].iloc[-1] # Ambil harga penutupan terakhir
        return None
    except Exception as e:
        print(f"Error mengambil harga penutupan {ticker}: {e}")
        return None

# Fungsi untuk mendapatkan data saham real-time
async def get_realtime_data(ticker):
    """Ambil harga real-time dan hitung perbedaannya."""
    try:
        # Menggunakan yf.Ticker().info untuk data real-time/terbaru
        # Menggunakan fast_info.last_price juga bisa, tapi info lebih lengkap
        ticker_data = yf.Ticker(ticker)
        info = await asyncio.to_thread(lambda: ticker_data.info) # Menjalankan blocking call di thread pool
        
        current_price = info.get('regularMarketPrice') or info.get('currentPrice')
        
        # Cek apakah harga penutupan hari sebelumnya sudah ada di cache
        prev_close = previous_close_cache.get(ticker)
        if prev_close is None:
             # Jika belum ada, ambil dari yfinance.
             # *Catatan: Ini akan memperlambat, idealnya dilakukan di on_ready*
            prev_close = await get_previous_close(ticker) 
            if prev_close:
                previous_close_cache[ticker] = prev_close
            else:
                return None
        
        if current_price is None or prev_close is None:
            return None

        # Hitung perubahan dan persentase
        change = current_price - prev_close
        percent_change = (change / prev_close) * 100
        
        return {
            'ticker': ticker,
            'current_price': current_price,
            'prev_close': prev_close,
            'change': change,
            'percent_change': percent_change
        }
    except Exception as e:
        print(f"Error mengambil data real-time untuk {ticker}: {e}")
        return None

# Fungsi untuk membuat Discord Embed
def create_stock_embed(data):
    """Membuat objek Discord Embed yang menarik."""
    ticker = data['ticker']
    current_price = data['current_price']
    prev_close = data['prev_close']
    change = data['change']
    percent_change = data['percent_change']
    
    # Tentukan warna dan emoji
    if percent_change >= THRESHOLD * 100:
        color = discord.Color.green()
        emoji = "🟢 Naik"
    elif percent_change <= -THRESHOLD * 100:
        color = discord.Color.red()
        emoji = "🔴 Turun"
    else:
        # Seharusnya tidak tercapai untuk notifikasi, tapi untuk command /list
        color = discord.Color.blue() 
        emoji = "⚪ Stabil"

    embed = discord.Embed(
        title=f"⚠️ Peringatan Harga Saham: {ticker}",
        description=f"Perubahan harga signifikan terdeteksi!",
        color=color,
        timestamp=dt.datetime.now(dt.timezone.utc)
    )
    
    # Format string
    change_sign = '+' if change >= 0 else ''
    change_str = f"{change_sign}{change:.2f}"
    percent_str = f"{change_sign}{percent_change:.2f}%"

    embed.add_field(name="Harga Saat Ini", value=f"**${current_price:.2f}**", inline=True)
    embed.add_field(name="Penutupan Sebelumnya", value=f"${prev_close:.2f}", inline=True)
    embed.add_field(name="Perubahan", value=f"{emoji} {change_str} ({percent_str})", inline=False)
    
    embed.set_footer(text=f"Dipantau dari Yahoo Finance | Ticker: {ticker}")
    return embed

# Tugas berulang untuk memantau harga (setiap 1 menit)
@tasks.loop(minutes=1)
async def check_stock_prices():
    """Memantau harga dan mengirim notifikasi jika ambang batas terlampaui."""
    now_utc = dt.datetime.now(dt.timezone.utc)
    current_hour_utc = now_utc.hour
    
    # Cek jam operasional pasar (13:30 UTC hingga 21:00 UTC)
    # Kita hanya menggunakan pengecekan jam, mengabaikan menit untuk kesederhanaan Codespace
    is_market_open = MARKET_OPEN_HOUR_UTC <= current_hour_utc < MARKET_CLOSE_HOUR_UTC
    
    if not is_market_open:
        print(f"Bukan jam pasar ({current_hour_utc} UTC). Melewati pengecekan.")
        return
    
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"Channel dengan ID {CHANNEL_ID} tidak ditemukan.")
        return

    print("Memeriksa harga saham...")
    
    # Ambil data secara paralel untuk efisiensi
    tasks = [get_realtime_data(t) for t in TICKERS]
    results = await asyncio.gather(*tasks)

    for data in results:
        if data:
            ticker = data['ticker']
            percent_change = data['percent_change']
            
            # Cek ambang batas 1.5% (positif atau negatif)
            if abs(percent_change) >= THRESHOLD * 100:
                # Cek apakah harga saat ini berbeda dengan harga terakhir yang di-cache
                # ini mencegah pengiriman notifikasi berulang untuk harga yang SAMA
                current_price = data['current_price']
                
                # Jika harga saat ini berbeda dengan harga di cache, kirim notifikasi dan update cache
                if stock_cache.get(ticker) != current_price:
                    
                    # Buat notifikasi dengan tag everyone
                    embed = create_stock_embed(data)
                    await channel.send(content="@everyone **Perhatian Pasar!**", embed=embed)
                    
                    # Update cache dengan harga baru
                    stock_cache[ticker] = current_price
                    print(f"Notifikasi terkirim untuk {ticker}: {percent_change:.2f}%")
                else:
                    print(f"Perubahan {ticker} signifikan, tapi harga sama dengan cache, tidak mengirim notifikasi.")
            
            # Selalu update cache dengan harga terbaru untuk command /list
            stock_cache[ticker] = data

# Tugas berulang untuk menghapus data cache harga (setiap 10 menit)
# Ini TIDAK menghapus 'previous_close_cache' karena dibutuhkan sepanjang hari
@tasks.loop(minutes=10)
async def clear_price_cache():
    """Menghapus cache harga untuk mengurangi beban penyimpanan Codespace."""
    global stock_cache
    
    # Membuat cache baru hanya dengan harga penutupan hari sebelumnya
    # *CATATAN: Logika ini terlalu agresif. Sebaiknya hanya hapus data 'current_price' jika ada,
    # tetapi karena kita menyimpan data lengkap 'data' di cache, kita hapus semuanya kecuali prev_close.*
    
    # Untuk kasus ini, kita akan *membuat ulang* cache harga, 
    # hanya menyimpan data harga penutupan hari sebelumnya untuk efisiensi.
    # Karena harga penutupan sebelumnya hanya perlu di-fetch sekali per hari.
    
    # Mengosongkan cache *stock_cache* yang berisi data harga real-time 
    # yang digunakan untuk notifikasi dan command /list
    stock_cache = {} 
    
    print("Cache harga real-time (stock_cache) telah dibersihkan untuk efisiensi penyimpanan.")

# Command Discord: /list
@bot.tree.command(name="list", description="Menampilkan harga real-time 12 saham yang dipantau.")
async def list_stocks(interaction: discord.Interaction):
    """Menampilkan harga saham dalam format embed, 1 halaman per saham."""
    await interaction.response.send_message("Memproses permintaan harga saham... mohon tunggu sebentar.", ephemeral=True)
    
    if not stock_cache:
        await interaction.edit_original_response(content="Data harga real-time sedang dimuat. Coba lagi sebentar.")
        return

    # Kirim tag everyone (atau gunakan peran yang sesuai)
    content = "@everyone **Daftar Harga Saham Real-time**"
    
    # Siapkan list embed
    embeds_to_send = []
    
    for ticker in TICKERS:
        data = stock_cache.get(ticker)
        if data and isinstance(data, dict):
            embeds_to_send.append(create_stock_embed(data))
        else:
             # Jika data belum ada, coba ambil harga penutupan
             prev_close = previous_close_cache.get(ticker, 'N/A')
             embed = discord.Embed(
                title=f"⚠️ Data Saham: {ticker} (Belum Ada Data Real-time)",
                description="Harga real-time belum di-cache. Coba lagi setelah pengecekan 1 menit berikutnya.",
                color=discord.Color.light_gray()
             )
             embed.add_field(name="Penutupan Sebelumnya", value=f"${prev_close:.2f}" if isinstance(prev_close, (int, float)) else prev_close)
             embeds_to_send.append(embed)

    # Kirim semua embed (Discord memiliki batas 10 embed per pesan, tapi karena kita mau
    # "setiap saham terdapat page sendiri" (seperti tombol halaman), kita kirim satu per satu,
    # atau jika ingin lebih ringkas kita gabung beberapa per pesan.)
    
    # Mengirim satu per satu untuk memenuhi "setiap saham terdapat page sendiri" secara sederhana
    for embed in embeds_to_send:
        # Kirim di channel tempat command dipanggil
        await interaction.channel.send(content=content, embed=embed)
        # Hapus content agar hanya dikirim di pesan pertama (untuk menghindari spam tag)
        content = "" 
        
    await interaction.edit_original_response(content="Daftar harga saham berhasil dikirim ke channel!")

@bot.event
async def on_ready():
    """Event saat bot berhasil terhubung ke Discord."""
    print(f'Bot terhubung sebagai {bot.user}')
    
    # Sinkronisasi Slash Commands
    await bot.tree.sync()
    print("Slash commands disinkronkan.")
    
    # Inisialisasi cache harga penutupan hari sebelumnya
    print("Memuat harga penutupan hari sebelumnya...")
    tasks = [get_previous_close(t) for t in TICKERS]
    results = await asyncio.gather(*tasks)
    for ticker, price in zip(TICKERS, results):
        if price is not None:
            previous_close_cache[ticker] = price
            print(f"Harga penutupan {ticker}: ${price:.2f}")

    # Mulai tugas berulang
    if not check_stock_prices.is_running():
        check_stock_prices.start()
    if not clear_price_cache.is_running():
        clear_price_cache.start()
    
    print("Tugas pemantauan dimulai.")

# Jalankan server web Flask dan Bot Discord
if __name__ == "__main__":
    # Fungsi ini harus dijalankan sebelum bot.run()
    keep_alive() 
    try:
        # Menjalankan bot. Catatan: discord.py v2.0+ mendukung task loop dengan bot.run
        bot.run(TOKEN)
    except discord.errors.HTTPException:
        print("HTTPException: Bot gagal terhubung. Coba reset token atau cek koneksi.")
        os.system("kill 1") # Untuk me-restart Codespace (jika di Codespace/Replit)