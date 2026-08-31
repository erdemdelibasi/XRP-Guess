# XRP Tahmin Paneli

Kişisel kullanım için: Binance'ın herkese açık piyasa verisini kullanarak XRP
için saatlik yön tahmini (artış/azalış) üreten, tahminleri loglayan ve başarı
oranını gösteren bir panel. iPhone'da "Ana Ekrana Ekle" ile app gibi çalışır.

**Bu bir yatırım tavsiyesi aracı değildir.** Binance hesabına hiç bağlanmaz,
API anahtarı istemez, otomatik alım-satım yapmaz — sadece izleme/tahmin amaçlıdır.

## Kurulum (tek seferlik)

### 1. Supabase (veritabanı)
1. https://supabase.com üzerinde ücretsiz bir proje oluştur.
2. Sol menüden **SQL Editor** → **New query** aç, [supabase/schema.sql](supabase/schema.sql)
   dosyasının içeriğini yapıştırıp çalıştır.
3. **Project Settings → API** sayfasından şunları not al:
   - `Project URL`
   - `anon` `public` key
   - `service_role` key (bunu **kimseyle paylaşma**, sadece GitHub Secrets'a girecek)

### 2. GitHub (kod deposu + saatlik zamanlayıcı)
1. Bu klasörü kendi GitHub hesabında yeni bir **private** repo olarak paylaş
   (`git remote add origin ...` ve `git push`).
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**
   ile şu ikisini ekle:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY` (service_role key)
3. **Actions** sekmesinden `Hourly XRP Prediction` workflow'unu aç, sağ üstten
   **Run workflow** ile bir kez manuel tetikleyip loglardan hatasız çalıştığını
   doğrula. Bu ilk çalışmada model henüz yoksa otomatik olarak "bootstrap"
   eğitimi yapılır (birkaç saniye sürer).
4. `Daily Model Retrain` workflow'unu da bir kez manuel çalıştır — bu, eğitilen
   modeli `backend/models/xrp_model.joblib` olarak repoya geri commit eder.

### 3. Parolayı belirle
Tarayıcı konsolunda (herhangi bir sekmede F12 → Console) şunu çalıştırıp
istediğin parolayı gir:
```js
crypto.subtle.digest("SHA-256", new TextEncoder().encode("BURAYA_PAROLAN"))
  .then(b => console.log(Array.from(new Uint8Array(b)).map(x => x.toString(16).padStart(2,"0")).join("")))
```
Çıkan hex değerini [frontend/config.js](frontend/config.js) içindeki
`PASSWORD_HASH` alanına yapıştır. Aynı dosyada `SUPABASE_URL` ve
`SUPABASE_ANON_KEY` alanlarını da (yalnızca **anon** key, service_role değil)
Supabase'ten aldığın değerlerle doldur ve değişikliği commit'leyip push'la.

> Not: `anon` key'in tarayıcı kodunda görünür olması normaldir — Supabase bu
> anahtarı public kullanım için tasarlar, gerçek koruma `schema.sql` içindeki
> Row Level Security (sadece SELECT) kurallarındadır. Parola koruması ise
> "gizli link" seviyesindedir, banka düzeyinde güvenlik değildir; URL'i
> kimseyle paylaşma.

### 4. Vercel (frontend barındırma)
1. https://vercel.com üzerinde GitHub hesabınla giriş yap, **New Project** ile
   bu repoyu içe aktar.
2. **Root Directory** olarak `frontend` klasörünü seç (framework: "Other" /
   statik site — build komutu gerekmez).
3. Deploy et. Sana `https://xxx.vercel.app` gibi bir adres verecek.

### 5. iPhone'a kur
1. Vercel adresini iPhone Safari'de aç, parolanı gir.
2. Paylaş menüsü → **Ana Ekrana Ekle**.
3. Artık ana ekrandan tam ekran bir uygulama gibi açılır.

## Nasıl çalışıyor
- Her saat başı (`.github/workflows/hourly_predict.yml`) GitHub Actions,
  Binance'tan XRP/BTC/ETH verisini çeker, teknik indikatör sinyali + ML model
  sinyalini birleştirip bir tahmin loglar ve bir önceki saatin tahminini
  gerçekleşen fiyatla karşılaştırıp doğru/yanlış olarak işaretler.
- Her gün (`daily_retrain.yml`) ML modeli güncel verilerle yeniden eğitilir ve
  teknik/ML bileşenlerinin son 14 günlük başarı oranına göre birleştirme
  ağırlıkları güncellenir — sistemin kendini zamanla ayarlaması bu şekilde olur.
- Panelde: güncel tahmin, doğru/yanlış pasta grafiği (24s/7g/30g/tümü
  filtreli) ve geçmiş tahmin tablosu gösterilir.

## Sınırlamalar
- Kripto fiyat tahmini doğası gereği belirsizdir; "kanıtlanmış" garanti bir
  yöntem yoktur. Bu araç olasılıksal sinyaller üretir ve başarısını şeffafça
  loglar — yatırım kararı tamamen sana aittir.
- Parola koruması client-side'dır, gelişmiş bir saldırgana karşı güvenlik
  sağlamaz; sadece rastgele erişimi engeller.
