# XRP Tahmin Paneli

Kişisel kullanım için: Binance'ın herkese açık piyasa verisi, XRP Ledger'ın
kamuya açık zincir verisi ve haber sentiment'ini birlikte kullanarak XRP için
her 15 dakikada bir bir sonraki çeyrek-saat işareti için yön (artış/azalış),
yüzde değişim ve hedef fiyat tahmini üreten, tahminleri loglayan ve başarı
oranını gösteren bir panel. iPhone'da "Ana Ekrana Ekle" ile app gibi çalışır.

Tahmin altı bağımsız kaynaktan gelen sinyalin ağırlıklı birleşimidir:
**teknik indikatörler** (RSI/MACD/EMA/Bollinger + BTC-ETH eşzamanlı ve
gecikmeli korelasyon + taker alım oranı), **ML modeli**, **XRPL balina/borsa
akışı** (zincir üzerinden büyük transferler), **haber/düzenleyici sentiment**
(SEC-Ripple davası gibi başlıklar), **emir defteri dengesizliği** (anlık
alım/satım derinliği) ve **Claude'un kendi bağımsız değerlendirmesi**
(Anthropic API'sine yapılan bir çağrıyla). Her birinin ağırlığı kendi geçmiş
isabet oranına göre günlük olarak yeniden ayarlanır.

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

   Claude sinyali için ise (bkz. aşağıda "Nasıl çalışıyor") kendi Anthropic
   API key'ini eklemen gerekir — **bu, projedeki tek ücretli bileşendir**,
   diğer beşi tamamen ücretsiz kamu API'leri kullanıyor:
   - https://console.anthropic.com adresinden bir hesap açıp faturalandırma
     (kredi kartı) ekle, **API Keys** sayfasından yeni bir key oluştur.
   - `ANTHROPIC_API_KEY` adıyla GitHub Secrets'a ekle.
   - Bu secret'ı eklemezsen sistem çökmez — Claude bileşeni sadece sürekli
     "sessiz" (nötr) kalır, diğer beş sinyal normal çalışmaya devam eder.
   - Yaklaşık maliyet: günde 96 çağrı (15 dakikada bir) × Claude Sonnet 5,
     her çağrı küçük bir metin (birkaç yüz token) olduğu için ayda ~$5-6
     civarı — sıfır değil ama ihmal edilebilir. Diğer beş sinyal tamamen
     ücretsiz kaldığı için bu, projedeki tek ücretli bileşen. (Daha ucuzu
     için `backend/claude_signal.py`'deki `MODEL`'i `claude-haiku-4-5`
     yapıp `output_config`'ten `effort` alanını kaldırman yeterli — o
     modelde bu parametre desteklenmiyor.)

   Günlük özet e-postası istiyorsan (bkz. aşağıda "Nasıl çalışıyor") şu
   üçünü de ekle:
   - `GMAIL_ADDRESS` — e-postanın gönderileceği Gmail adresin
     (ör. `erdemdelibasi@gmail.com`)
   - `GMAIL_APP_PASSWORD` — normal Gmail şifren **değil**: önce
     https://myaccount.google.com/security adresinden **2 Adımlı
     Doğrulama**'yı aç (kapalıysa), sonra
     https://myaccount.google.com/apppasswords adresinden yeni bir
     "Uygulama Şifresi" oluştur (16 haneli, boşluksuz gir)
   - `REPORT_RECIPIENT` — maili alacak adres (genelde `GMAIL_ADDRESS` ile
     aynı, kendine gönderir)

   Bunları eklemezsen sadece `Daily Email Report` workflow'u başarısız olur
   (Actions sekmesinde kırmızı görünür); tahmin/al-sat sistemini etkilemez.
3. **Actions** sekmesinden `Quarter-Hourly XRP Prediction` workflow'unu aç, sağ
   üstten **Run workflow** ile bir kez manuel tetikleyip loglardan hatasız
   çalıştığını doğrula. Bu ilk çalışmada model henüz yoksa otomatik olarak
   "bootstrap" eğitimi yapılır (geçmiş 15 dakikalık mumlarla, biraz sürebilir).
4. `Daily Model Retrain` workflow'unu da bir kez manuel çalıştır — bu, eğitilen
   modeli `backend/models/xrp_model.joblib` olarak repoya geri commit eder.
5. `Daily Email Report` workflow'unu manuel çalıştırıp (Gmail secret'larını
   ekledikten sonra) günlük özet mailinin gerçekten geldiğini doğrula.

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
  ve altı bağımsız sinyali hesaplar:
  - **Teknik** (`indicators.py`): RSI, MACD, EMA kesişimi, Bollinger, hacim,
    BTC/ETH ile eşzamanlı korelasyon, BTC/ETH'nin XRP'yi kaç periyot
    (15dk-2sa) önden yönlendirdiğini bulan gecikmeli (lead-lag) korelasyon,
    ve "taker buy ratio" (bir mumdaki hacmin ne kadarının agresif alım
    olduğu — sadece fiyat şeklinden değil, kimin işlemi başlattığından gelen
    bir sinyal).
  - **Emir defteri dengesizliği** (`orderbook_signal.py`): Binance'ın anlık
    emir defteri görüntüsünden (`/api/v3/depth`, anahtar gerekmez) alış/satış
    tarafındaki hacim dengesizliğini ölçer. Bu **anlık bir görüntüdür**,
    geçmiş arşivi yoktur — bu yüzden ayrı bir üst-seviye bileşen olarak
    tutulur ve backtest'e dahil edilemez, sadece canlı çalışır.
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
  - **Claude** (`claude_signal.py`): teknik sinyalin ve balina akışının o
    anki ham değerleriyle, o sıradaki gerçek XRP/Ripple haber başlıklarının
    metnini (haber sinyalinin anahtar-kelime skoru değil, başlıkların
    kendisi) Anthropic API'sine (Claude Sonnet 5) gönderip bağımsız bir
    yön/güven değerlendirmesi ister. Projedeki **tek ücretli** bileşen —
    kendi `ANTHROPIC_API_KEY`'in yoksa (bkz. yukarıda kurulum) sürekli
    sessiz kalır, sistemin geri kalanını etkilemez. Diğer canlı-only
    sinyaller (balina/haber/emir-defteri) gibi bu da ucuza geriye test
    edilemez — gerçek değeri ancak haftalarca canlı veri birikince görülür.

  Bu altı sinyal `ensemble.py` içinde, her birinin kendi geçmiş isabet
  oranıyla orantılı ağırlıklarla birleştirilip **bir sonraki çeyrek-saat
  işareti** (örn. 18:07'de çalışırsa 18:15'i) için yön, yüzde değişim ve
  hedef fiyat tahmini olarak loglanır. Bir saat içinde böylece 4 ayrı tahmin
  birikir (18:15, 18:30, 18:45, 19:00 gibi). Aynı çalışma, hedef zamanı
  gelmiş önceki tahminleri gerçekleşen fiyatla karşılaştırıp doğru/yanlış
  olarak işaretler (altı bileşenin her biri için ayrı ayrı).
- Her gün (`daily_retrain.yml`) ML modeli ~41 günlük 15-dakikalık geçmişle
  yeniden eğitilir ve altı bileşenin de son 14 günlük başarı oranına göre
  birleştirme ağırlıkları güncellenir — sistemin kendini zamanla ayarlaması
  bu şekilde olur. Balina/haber gibi çoğu zaman sessiz kalan bileşenler için
  yeterli "konuştuğu" örnek birikene kadar (asgari 20 sessiz-olmayan
  tahmin) ağırlıklar varsayılan değerlerde kalır.
- Aynı çalışma, nihai (ensemble) tahminle `trading.py` üzerinden **sanal**
  bir yeniden dengeleme de tetikler. Eskiden ya tamamen nakit ya tamamen XRP
  olacak şekilde ikili çalışıyordu; artık **güven-bazlı pozisyon
  büyüklüğü** kullanıyor: hedef XRP oranı `min(güven/0.25, 1) × %85` ile
  hesaplanır (yani güven ne kadar yüksekse portföyün o kadar büyük bir
  kısmı XRP'ye ayrılır, tek sinyalle asla %85'i geçmez). Mevcut oran
  hedeften portföy değerinin %25'inden fazla sapmadıkça işlem yapılmaz
  (komisyon erozyonunu önlemek için — ilk denemede %10 eşik kullanılmıştı
  ama `backtest.py` bunun aşırı sık işleme yol açtığını gösterdi). Ayrıca
  bir **stop-loss** var: portföy değeri kendi tüm-zamanların zirvesinden
  %15 düşerse yöne bakılmaksızın tamamı nakde çevrilir (zirve asla geriye
  düşmez — fiyat toparlanmadan yeniden pozisyon açılırsa aynı stop-loss'a
  kısa sürede tekrar takılabilir, bu beklenen bir davranıştır). Her işlemde
  Binance'ın standart %0.10'luk spot komisyonu düşülür. Gerçek para veya
  Binance hesabı kesinlikle karışmaz.
- `backend/backtest.py` (`workflow_dispatch` ile elle çalıştırılır, zamanlanmış
  değildir) sadece teknik+ML bileşenlerini (balina/haber/emir-defteri
  canlı-only, geçmiş arşivi yok) ~83 günlük geçmiş veriyle, look-ahead
  olmadan (her adımda sadece son 400 mumluk pencere) geriye test eder ve
  aynı pozisyon büyüklüğü/stop-loss mantığını uygular. Al-ve-tut ve sabit
  nakit taban çizgileriyle karşılaştırır — canlı sistemdeki "isabet oranı"
  rakamının ne kadar güvenilir olduğunu bağımsızca kontrol etmenin yolu
  budur (canlının günlük yeniden-eğitimi/kendi kendini ayarlayan ağırlıkları
  tekrar oynatılmaz, bu yüzden birebir aynı sonucu vermez — bkz. betiğin
  başındaki sınırlamalar).
- Panelde: güncel tahmin (hedef zaman, yüzde değişim, tahmini fiyat, teknik/ML
  kırılımı), doğru/yanlış pasta grafiği (24s/7g/30g/tümü filtreli), sanal
  portföyün canlı değeri ve işlem geçmişi, ve geçmiş tahmin tablosu gösterilir.
- Her gün saat 18:10'da (Türkiye saati, `daily_report.yml`) son 24 saatin
  (dün 18:00 - bugün 18:00) özeti e-posta ile gönderilir: kaç tahmin
  yapıldı/sonuçlandı/doğru çıktı, hangi bileşen (teknik/ML/balina/haber) o
  gün en isabetliydi, sanal portföyün bakiyesi ve XRP fiyatı dünden bugüne
  nasıl değişti, ve güncel ensemble ağırlıkları. Bu, `trades` ve
  `predictions` tablolarındaki geçmiş kayıtlardan geriye dönük olarak
  hesaplanır — ayrı bir "günlük anlık görüntü" tablosu tutulmaz.

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
- Claude sinyali de balina/haber/emir-defteri gibi canlı-only'dir — ucuza
  geriye test edilemez (her adım gerçek, ücretli bir API çağrısı gerektirir),
  bu yüzden `backtest.py`'a dahil değildir ve "işe yarıyor mu" sorusunun
  cevabı ancak haftalarca canlı sonuç birikince netleşir.
- `backtest.py`'ın ilk ~83 günlük çalıştırması dürüst bir sonuç verdi: o
  belirli pencerede teknik+ML yön isabeti %49 (rastgele tahminden bile
  kötü) çıktı ve strateji al-ve-tut'un (XRP o pencerede güçlü yükseldiği
  için) çok gerisinde kaldı. Bu, "sistem çalışıyor" iddiasını değil,
  backtest'in gerçekten işe yaradığını gösteriyor — canlıdaki isabet oranı
  rakamına körü körüne güvenmemek gerektiğinin somut kanıtı. Farklı zaman
  pencerelerinde farklı sonuçlar çıkabilir; düzenli olarak yeniden
  çalıştırıp takip etmek gerekir.
