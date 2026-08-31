# XRP Tahmin Paneli

Kişisel kullanım için: Binance'ın herkese açık piyasa verisini kullanarak XRP
için her 15 dakikada bir bir sonraki çeyrek-saat işareti için yön (artış/azalış),
yüzde değişim ve hedef fiyat tahmini üreten, tahminleri loglayan ve başarı
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

### 2. GitHub (kod deposu + 15 dakikalık zamanlayıcı)
1. Bu klasörü kendi GitHub hesabında yeni bir **private** repo olarak paylaş
   (`git remote add origin ...` ve `git push`).
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**
   ile şu ikisini ekle:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY` (service_role key)
3. **Actions** sekmesinden `Quarter-Hourly XRP Prediction` workflow'unu aç, sağ
   üstten **Run workflow** ile bir kez manuel tetikleyip loglardan hatasız
   çalıştığını doğrula. Bu ilk çalışmada model henüz yoksa otomatik olarak
   "bootstrap" eğitimi yapılır (geçmiş 15 dakikalık mumlarla, biraz sürebilir).
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
- Her 15 dakikada bir (`.github/workflows/predict.yml`, `:01/:16/:31/:46`)
  GitHub Actions, Binance'tan XRP/BTC/ETH'nin 15 dakikalık mum verisini çeker,
  teknik indikatör sinyali + ML model sinyalini birleştirir ve **bir sonraki
  çeyrek-saat işareti** (örn. 18:07'de çalışırsa 18:15'i) için yön, yüzde
  değişim ve hedef fiyat tahmini loglar. Bir saat içinde böylece 4 ayrı
  tahmin birikir (18:15, 18:30, 18:45, 19:00 gibi). Aynı çalışma, hedef zamanı
  gelmiş önceki tahminleri gerçekleşen fiyatla karşılaştırıp doğru/yanlış
  olarak işaretler.
- Her gün (`daily_retrain.yml`) ML modeli ~41 günlük 15-dakikalık geçmişle
  yeniden eğitilir ve teknik/ML bileşenlerinin son 14 günlük başarı oranına
  göre birleştirme ağırlıkları güncellenir — sistemin kendini zamanla
  ayarlaması bu şekilde olur.
- Panelde: güncel tahmin (hedef zaman, yüzde değişim, tahmini fiyat, teknik/ML
  kırılımı), doğru/yanlış pasta grafiği (24s/7g/30g/tümü filtreli) ve geçmiş
  tahmin tablosu gösterilir.

## Sınırlamalar
- Kripto fiyat tahmini doğası gereği belirsizdir; "kanıtlanmış" garanti bir
  yöntem yoktur. Bu araç olasılıksal sinyaller üretir ve başarısını şeffafça
  loglar — yatırım kararı tamamen sana aittir.
- Gösterilen yüzde değişim/hedef fiyat, ayrı bir fiyat regresyon modelinin
  çıktısı değildir; yön sinyalinin gücü, XRP'nin güncel volatilitesiyle
  ölçeklenerek türetilen bir **tahmini** büyüklüktür (yani "bu kadar emin
  isem, tipik olarak bu kadarlık bir hareket beklenir" mantığı). Kesin bir
  fiyat vaadi değildir.
- Parola koruması client-side'dır, gelişmiş bir saldırgana karşı güvenlik
  sağlamaz; sadece rastgele erişimi engeller.
