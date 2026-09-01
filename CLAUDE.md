# XRP Tahmin Paneli

Kişisel kullanım için XRP fiyat tahmin/izleme PWA'sı. Binance genel piyasa
verisini kullanır, **hiçbir Binance API anahtarı yok, gerçek emir yok**.
Portföy özelliği tamamen sanal/kağıt üzerindedir ($1000 simülasyon).

## Mimari

```
GitHub Actions (cron, sunucusuz zamanlayıcı)
  -> backend/predict.py     her 15 dk (:01/:16/:31/:46) çalışır
  -> backend/retrain.py     her gün 03:30 UTC çalışır
       |
       v
Supabase (Postgres + otomatik REST API, RLS ile korunur)
       |
       v
Vercel (frontend/ statik hosting, GitHub push'unda otomatik deploy)
```

- **Backend**: Python, `backend/` altında. `requirements.txt`'de bağımlılıklar.
- **Frontend**: framework yok, saf HTML/CSS/JS, `frontend/` altında. `config.js`
  gerçek Supabase URL + anon key + parola hash'i içerir (bilerek — anon key
  public kullanım için tasarlanmıştır, gerçek koruma RLS'dedir).
- **Veritabanı şeması**: `supabase/schema.sql` — tek doğruluk kaynağı. Şemada
  değişiklik yaparsan bu dosyayı güncelle VE kullanıcıya Supabase SQL
  Editor'de çalıştırması gereken migration'ı ayrıca ver (repo'dan otomatik
  uygulanmaz).

## Önemli kısıtlar

- **Binance hesabına asla bağlanma.** Sadece `data-api.binance.vision`
  (public, key'siz, CORS açık) kullanılır — `api.binance.com` değil, çünkü
  cloud CI runner'ları IP bazlı engellenebiliyor.
- **Gerçek para/emir yok.** `backend/trading.py` tamamen simülasyon;
  `portfolio_state`/`trades` tabloları sanal.
- Tahminler **çeyrek-saat işaretlerini** (:00/:15/:30/:45) hedefler, çalışma
  anından "1 saat sonra"yı değil. Bkz. `predict.py:next_quarter_hour`.
- Yön sinyali dört bağımsız bileşenin (`ensemble.COMPONENTS`) ağırlıklı
  ortalamasıdır: teknik indikatör (`indicators.py`, BTC/ETH lead-lag dahil),
  ML model (`ml_model.py`), XRPL balina/borsa akışı (`whale_signal.py`,
  XRPSCAN public API) ve haber/düzenleyici sentiment (`news_signal.py`,
  Google News RSS). **İkisi de anahtar gerektirmez, tamamen ücretsiz** —
  CryptoPanic denendi ama ücretsiz katmanını kaldırmış (en ucuz plan
  $50/hafta), o yüzden Google News RSS'e geçildi.
  Ağırlıklar `retrain.py` tarafından günlük olarak son 14 günlük başarı
  oranına göre güncellenir (`model_state` tablosu). `ensemble.recompute_weights`
  her bileşeni **bağımsız** olarak günceller — bir bileşenin (ör. news)
  henüz yeterli geçmişi yoksa sadece o bileşen varsayılan ağırlıkta kalır,
  diğerlerinin kendi aralarında ayarlanmasını engellemez (bu bilinçli bir
  düzeltme — ilk versiyon yanlışlıkla TÜM bileşenler hazır olana kadar
  hiçbirini güncellemiyordu). `whale`/`news` çoğu 15dk'da "sessiz"
  (confidence=0) kalır — bu bir hata değil, `retrain.py` bu satırları isabet
  oranına dahil etmiyor (abstention). Yeni bir bileşen eklemek istersen
  `ensemble.COMPONENTS`'e ekleyip `predictions` tablosuna aynı 5-kolonluk
  örüntüyü (`{c}_direction/confidence/pct_change/price/correct` +
  `weight_{c}`) uygulaman yeterli.

## Geliştirme notları

- Git kimliği kullanıcının makinesinde ayarlı (`erdemdelibasi@gmail.com`) —
  Vercel bunu doğrulanmış GitHub e-postasıyla eşleştirip deploy'u
  bloklayabiliyor, eşleşmezse deploy "Blocked" görünür.
- `frontend/sw.js` network-first stratejisiyle çalışır; cache adı
  (`CACHE_NAME`) değişmedikçe eski JS/HTML takılı kalabilir — frontend'de
  büyük bir davranış değişikliği yaptıysan `CACHE_NAME`'i artır.
- Supabase REST API varsayılan olarak ~1000 satırla sınırlı döner; geniş
  tarih aralığı sorgularında `.range()` ile sayfalama gerekir (bkz.
  `retrain.py:_fetch_all_since`).
- Doğrulama genelde `curl` ile Supabase REST API'sine (public anon key ile)
  doğrudan sorgu atarak yapılır — kullanıcıdan ekran görüntüsü istemeden
  önce bunu dene.
- Workflow'ları manuel tetiklemek için: GitHub repo → Actions →
  ilgili workflow → "Run workflow".

## Ton / dil

- Kullanıcıyla iletişim Türkçe; kod/tanımlayıcılar İngilizce.
- Uygulama bir yatırım tavsiyesi aracı değildir — bu uyarı README ve UI'da
  görünür kalmalı, kaldırılmamalı.
