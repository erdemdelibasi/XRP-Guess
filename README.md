# XRP Tahmin Paneli

Kişisel kullanım için: Binance'ın herkese açık piyasa verisi, XRP Ledger'ın
kamuya açık zincir verisi ve haber sentiment'ini birlikte kullanarak XRP için
her 15 dakikada bir bir sonraki çeyrek-saat işareti için yön (artış/azalış),
yüzde değişim ve hedef fiyat tahmini üreten, tahminleri loglayan ve başarı
oranını gösteren bir panel. iPhone'da "Ana Ekrana Ekle" ile app gibi çalışır.

Tahmin dört bağımsız kaynaktan gelen sinyalin ağırlıklı birleşimidir:
**teknik indikatörler** (RSI/MACD/EMA/Bollinger + BTC-ETH eşzamanlı ve
gecikmeli korelasyon), **ML modeli**, **XRPL balina/borsa akışı** (zincir
üzerinden büyük transferler) ve **haber/düzenleyici sentiment** (SEC-Ripple
davası gibi başlıklar). Her birinin ağırlığı kendi geçmiş isabet oranına göre
günlük olarak yeniden ayarlanır.

**Bu bir yatırım tavsiyesi aracı değildir.** Binance hesabına hiç bağlanmaz,
API anahtarı istemez, gerçek para veya gerçek emirle hiçbir şekilde
ilişkilendirilmez. Sistem kendi tahminlerini test etmek için $1000'lık
**sanal** bir portföyle otomatik alım-satım simülasyonu yapar (bkz. aşağı) —
bu tamamen kağıt üzerinde bir deneydir, gerçek hesabına dokunmaz.

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

   Balina sinyali (XRPSCAN) ve haber sinyali (Google News RSS) için ayrıca
   bir anahtar gerekmez, ikisi de tamamen ücretsiz ve herkese açık.
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
  GitHub Actions, Binance'tan XRP/BTC/ETH'nin 15 dakikalık mum verisini çeker
  ve dört bağımsız sinyali hesaplar:
  - **Teknik** (`indicators.py`): RSI, MACD, EMA kesişimi, Bollinger, hacim,
    BTC/ETH ile eşzamanlı korelasyon, ve BTC/ETH'nin XRP'yi kaç periyot
    (15dk-2sa) önden yönlendirdiğini bulan gecikmeli (lead-lag) korelasyon.
  - **ML** (`ml_model.py`): geçmiş 15-dakikalık verilerle eğitilmiş bir
    sınıflandırıcı.
  - **Balina/borsa akışı** (`whale_signal.py`): XRP Ledger tamamen herkese
    açık olduğu için, XRPSCAN'ın public API'siyle bilinen borsa cüzdanlarına
    (Binance dahil) giren/çıkan büyük (100.000+ XRP) transferler izlenir.
    Borsaya net giriş → düşüş sinyali (satış öncesi yatırma davranışı olarak
    yorumlanır), net çıkış → yükseliş sinyali (biriktirme). Çoğu 15 dakikalık
    pencerede böyle büyük bir hareket olmaz, bu durumda sinyal **sessiz**
    (nötr) kalır — bu beklenen bir davranıştır, hata değildir.
  - **Haber/düzenleyici sentiment** (`news_signal.py`): Google News'in
    ücretsiz RSS aramasından son birkaç saatteki XRP/Ripple başlıklarını
    çeker, anahtar kelime eşleşmesinden (approve/lawsuit/win/fine vb.) bir
    skor üretir; SEC/dava/onay gibi düzenleyici kelimeler geçen başlıklara
    ekstra ağırlık verir (XRP fiyatını tarihsel olarak en çok hareket
    ettiren haber türü budur). Oy tabanlı bir sentiment API'si olmadığı için
    diğer bileşenlere göre daha zayıf/gürültülü bir sinyaldir — bu yüzden
    ensemble'daki payı küçük tutulur. Eşleşen başlık yoksa sessiz kalır.

  Bu dört sinyal `ensemble.py` içinde, her birinin kendi geçmiş isabet
  oranıyla orantılı ağırlıklarla birleştirilip **bir sonraki çeyrek-saat
  işareti** (örn. 18:07'de çalışırsa 18:15'i) için yön, yüzde değişim ve
  hedef fiyat tahmini olarak loglanır. Bir saat içinde böylece 4 ayrı tahmin
  birikir (18:15, 18:30, 18:45, 19:00 gibi). Aynı çalışma, hedef zamanı
  gelmiş önceki tahminleri gerçekleşen fiyatla karşılaştırıp doğru/yanlış
  olarak işaretler (dört bileşenin her biri için ayrı ayrı).
- Her gün (`daily_retrain.yml`) ML modeli ~41 günlük 15-dakikalık geçmişle
  yeniden eğitilir ve dört bileşenin de son 14 günlük başarı oranına göre
  birleştirme ağırlıkları güncellenir — sistemin kendini zamanla ayarlaması
  bu şekilde olur. Balina/haber gibi çoğu zaman sessiz kalan bileşenler için
  yeterli "konuştuğu" örnek birikene kadar (asgari 20 sessiz-olmayan
  tahmin) ağırlıklar varsayılan değerlerde kalır.
- Aynı çalışma, nihai (ensemble) tahmin yön değiştirdiğinde ve güven eşiğini
  geçtiğinde `trading.py` üzerinden **sanal** bir alım/satım da tetikler:
  Artış'a dönünce elde nakit varsa tüm nakitle XRP alınır, Azalış'a dönünce
  elde XRP varsa tamamı satılır (Binance'ın standart %0.10'luk spot işlem
  komisyonu her işlemde düşülür). Aynı yönde kaldığı sürece işlem yapılmaz —
  yoksa komisyonlar her 15 dakikada bir portföyü eritirdi. Gerçek para veya
  Binance hesabı kesinlikle karışmaz.
- Panelde: güncel tahmin (hedef zaman, yüzde değişim, tahmini fiyat, teknik/ML
  kırılımı), doğru/yanlış pasta grafiği (24s/7g/30g/tümü filtreli), sanal
  portföyün canlı değeri ve işlem geçmişi, ve geçmiş tahmin tablosu gösterilir.

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
- Balina sinyali, izlenen her borsa cüzdanı için XRPSCAN'da en fazla 6 sayfa
  (150 işlem) geriye gider; olağanüstü yoğun bir cüzdanda bu bile 120
  dakikalık pencerenin tamamını kapsamayabilir (yerel testte gerçek veriyle
  doğrulandı — bkz. commit geçmişi).
- Haber sinyali oy tabanlı bir sentiment kaynağı değil, sadece başlık
  metninde anahtar kelime araması yapıyor — bu nedenle diğer üç bileşene
  göre daha kaba/gürültülü bir tahmindir; ensemble'daki payının küçük
  tutulması ve kendi isabet geçmişi birikmeden ağırlığının artmaması
  bilinçli bir tasarım kararıdır.
- "Borsaya giriş=düşüş, çıkış=yükseliş" ve haber-başlık sentiment'i,
  akademik literatürde sıkça kullanılan ama kesinliği kanıtlanmamış sezgisel
  (heuristic) yorumlardır — teknik/ML sinyalleri gibi bunlar da olasılıksaldır.
