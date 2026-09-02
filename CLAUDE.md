# XRP-Guess

Kişisel kullanım için XRP fiyat tahmin/izleme PWA'sı. Binance genel piyasa
verisini kullanır, **hiçbir Binance API anahtarı yok, gerçek emir yok**.
Portföy özelliği tamamen sanal/kağıt üzerindedir ($1000 simülasyon).

## Mimari

```
GitHub Actions (cron, sunucusuz zamanlayıcı)
  -> backend/predict.py       her 15 dk (:01/:16/:31/:46) çalışır
  -> backend/retrain.py       her gün 03:30 UTC çalışır
  -> backend/daily_report.py  her gün 18:10 TRT (15:10 UTC) çalışır, Gmail SMTP ile mail atar
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
  `portfolio_state`/`trades` tabloları sanal. Pozisyon büyüklüğü
  güven-bazlı (`_target_allocation`, ikili CASH/LONG değil), stop-loss'u
  var (`STOP_LOSS_DRAWDOWN`, `peak_value` hiç geri düşmez). Stop-loss
  sonrası `STOP_LOSS_COOLDOWN_CANDLES` (~10 saat) kadar yeniden pozisyon
  açılmaz — `peak_value` hiç düşmediği için, cooldown olmadan küçük bir
  yeniden-giriş neredeyse her zaman bir sonraki mumda aynı stop-loss'u
  tekrar tetikliyordu (60 günlük bir backtest'te 236 stop-loss'un 235'i
  10 saat içinde tekrar stop-loss'a çarpıyordu, sermayenin ~%35'i sadece
  komisyona gidiyordu — bkz. `portfolio_state.stop_loss_cooldown`). Karar
  mantığı `compute_rebalance()` içinde DB'siz saf bir fonksiyon olarak
  yaşıyor —
  hem `maybe_trade()` (canlı) hem `backend/backtest.py` (geçmiş replay)
  aynı fonksiyonu çağırır, mantık iki yerde tekrarlanmaz. Yeni bir eşik/
  parametre değiştirirsen `backtest.py`'ı çalıştırıp etkisini gerçekten
  ölç — `REBALANCE_THRESHOLD` ilk denemede %10 iken backtest'te aşırı
  komisyon erozyonuna yol açtığı görülüp %25'e çıkarıldı, sezgiyle değil
  ölçümle karar verildi.
- `backend/backtest.py` (`workflow_dispatch`, cron yok) sadece
  technical+ML'i test eder — whale/news/orderbook canlı-only. İlk ~83
  günlük çalıştırma dürüst ama çarpıcı bir sonuç verdi: %49 yön isabeti
  (rastgeleden kötü) ve al-ve-tut'un çok altında getiri. Bunu "sistem
  bozuk" diye yorumlama ya da gizleme — bu backtest'in görevini yaptığının
  kanıtı; README'nin Sınırlamalar bölümünde kullanıcıya da aynen aktarıldı.
  20.000 mumluk (~208 gün, ~5800 test adımı) daha büyük bir pencerede de
  aynı sonuç doğrulandı: yön isabeti tam %50.0, Brier Skill Score negatif.
  Rapor artık Brier score/log-loss ve güven aralığına göre bir kalibrasyon
  tablosu (reliability diagram) da basıyor — küçük (~2000 adım altı)
  pencerelerde bu tablo gürültülü/yanıltıcı çıkabiliyor (örn. "güven
  arttıkça isabet düşüyor" gibi sahte bir ters-ilişki gördük, büyük
  pencerede kayboldu), o yüzden kalibrasyonla ilgili bir sonucu asla tek
  bir küçük backtest koşusuna dayandırma.
- Tahminler **çeyrek-saat işaretlerini** (:00/:15/:30/:45) hedefler, çalışma
  anından "1 saat sonra"yı değil. Bkz. `predict.py:next_quarter_hour`.
- Yön sinyali altı bağımsız bileşenin (`ensemble.COMPONENTS`) ağırlıklı
  ortalamasıdır: teknik indikatör (`indicators.py`, BTC/ETH lead-lag +
  taker buy ratio dahil), ML model (`ml_model.py`, aynı taker buy ratio bir
  FEATURE_COLUMNS girdisi olarak da kullanılır), XRPL balina/borsa akışı
  (`whale_signal.py`, XRPSCAN public API), haber/düzenleyici sentiment
  (`news_signal.py`, Google News RSS — CryptoPanic denendi ama ücretsiz
  katmanını kaldırmış, $50/hafta'dan başlıyor, o yüzden vazgeçildi), emir
  defteri dengesizliği (`orderbook_signal.py`, Binance `/api/v3/depth`), ve
  Claude'un kendi bağımsız değerlendirmesi (`claude_signal.py`) — teknik ve
  balina bileşenlerinin ham sinyalini + gerçek son haber başlıklarını
  (`news_signal.recent_headlines()`, sadece haber bileşeninin anahtar-kelime
  skoru değil) tek bir Messages API çağrısıyla Claude'a verip bağımsız bir
  yön/güven yargısı istiyor (`output_config.effort="low"`, düşük maliyetli
  sınıflandırma-benzeri bir görev olduğu için). İlk beşi hiç anahtar
  gerektirmez; `claude` kendi `ANTHROPIC_API_KEY`'ine ihtiyaç duyar (repo
  secret, Supabase/Binance'ten ayrı) — secret yoksa sistem çökmez, `claude`
  sadece sürekli nötr (confidence=0) kalır. Model `claude_signal.MODEL`'de
  sabit (`claude-sonnet-5`) — daha ucuz Haiku 4.5 ile denendi ama kalite
  için tekrar Sonnet 5'e çevrildi, günde 96 çağrı (15 dk'da bir) ile ~$5-6/ay
  civarı. Hepsi (`claude` dahil) API/parse hatasında sessizce nötre düşer,
  bir bileşenin hıçkırığı tahmin döngüsünü hiç bloklamaz. **`orderbook` (ve
  `whale`/`news`/`claude`) canlı-only — geçmiş arşivi yok (Claude'u ucuza
  geriye test etmenin bir yolu da yok, paralı bir LLM çağrısını geçmişe karşı
  tekrar oynatmak pahalı olurdu), bu yüzden `backend/backtest.py` sadece
  technical+ML'i test edebilir**, bu sınırlama backtest raporunda açıkça
  belirtilir.
  Ağırlıklar `retrain.py` tarafından günlük olarak son 14 günlük başarı
  oranına göre güncellenir (`model_state` tablosu) ve ham haliyle **her
  zaman** tam %100'e tamamlanır (float toplamı) — ekranda %100 etmiyormuş
  gibi görünüyorsa veri değil gösterim sorunudur: `app.js`'de her ağırlık
  bağımsız `Math.round`'lanıyordu, hem bu yüzden ~1 puan kayabiliyordu hem
  de `orderbook` gösterime hiç dahil değildi. `roundWeightsTo100()`
  (en-büyük-kalan yöntemi) ve `orderbook`'un eklenmesiyle düzeltildi.
  `ensemble.recompute_weights`
  her bileşeni **bağımsız** olarak günceller — bir bileşenin (ör. news)
  henüz yeterli geçmişi yoksa sadece o bileşen varsayılan ağırlıkta kalır,
  diğerlerinin kendi aralarında ayarlanmasını engellemez. `whale`/`news`/
  `orderbook` sık sık "sessiz" (confidence=0) kalır — bu bir hata değil,
  `retrain.py` bu satırları isabet oranına dahil etmiyor (abstention). Yeni
  bir bileşen eklemek istersen `ensemble.COMPONENTS`+`COLUMN_PREFIX`'e
  ekleyip `predictions` tablosuna aynı 5-kolonluk örüntüyü
  (`{prefix}_direction/confidence/pct_change/price/correct` + `weight_{c}`)
  uygulaman yeterli — `retrain.py`/`daily_report.py` tamamen bu listeler
  üzerinden döngü kurduğu için başka kod değişikliği gerekmez. **Ama
  `model_state` tablosunda geçici bir tutarsızlık penceresi var**:
  `predict.py:get_ensemble_weights` `model_state`'teki satırları olduğu
  gibi `DEFAULT_WEIGHTS` üzerine yazıyor, toplamın 1.0 olduğunu
  doğrulamıyor. `claude` eklendiğinde (2026-09-02) tam bunun kanıtı
  yaşandı: o sabahki `retrain.py` koşusu henüz sadece eski 5 bileşeni
  biliyordu ve onlar zaten kendi aralarında %100'e tamamlanacak şekilde
  yazılmıştı; `claude` satırı bundan saatler sonra (şema migration'ıyla
  birlikte, varsayılan 0.15 ile) ayrıca eklenince toplam %115'e çıktı —
  Supabase'e curl atıp doğrulandı, "Daily Model Retrain" workflow'u elle
  tetiklenerek düzeltildi (bir sonraki `retrain.py`, `COMPONENTS`'teki
  **tüm** bileşenleri aynı anda `recompute_weights`'ten geçirip
  `model_state`'e tutarlı, toplamı-1.0 bir set yazdığı için kendi kendine
  de düzelirdi). **İleride yeni bir bileşen daha eklersen**: eklendiği gün
  ile bir sonraki günlük retrain arasında ağırlık toplamı geçici olarak
  %100'ü aşabilir (ya da varsayılan yüzdesi kadar eksik kalabilir) —
  bu beklenen bir geçiş durumudur, hemen "bug" deyip `get_ensemble_weights`
  içine normalize mantığı ekleme; istersen retrain workflow'unu elle
  tetikleyerek anında düzeltebilirsin.
  **`whale`/`news` canlı veride hep UP diyordu** (news 25/25, whale 24/27) —
  kalibrasyon değil, iki ayrı mantık hatasıydı: `news_signal.py`'de kısa
  anahtar kelimeler (`ban`, `sue`, `sec`, `hack`) substring eşleşmesiyle
  yanlış kelimelerin içinde de yakalanıyordu (`"ban"` → `"banking"` içinde),
  düzeltildi (kelime-sınırı eşleşmesi + kaybolan çekim biçimleri elle
  eklendi — `_keyword_hits()`). `whale_signal.py`'de XRPSCAN'de 672 borsa
  etiketli hesap varken kod sadece ilk 8 eşleşmeyi alıyordu ve bunların
  8'i de Binance çıkıyordu (Coinbase 552, OKX 1 hesabı hiç izlenmiyordu) —
  artık `get_exchange_accounts()` borsalar arası round-robin yapıyor. Bu
  düzeltmelerin gerçek etkisi ancak birkaç haftalık yeni canlı veriyle
  görülebilir (backtest edilemiyorlar) — `predictions` tablosundan tekrar
  kontrol etmeden "düzeldi" deme.
- **Model uyumluluk kontrolü şart**: `FEATURE_COLUMNS`'a yeni bir özellik
  eklersen, repoda committed duran eski `models/xrp_model.joblib` artık
  uyumsuz olur. `predict.py`, modelin `n_features_in_`'ini
  `len(ml_model.FEATURE_COLUMNS)` ile karşılaştırıp uyuşmazsa otomatik
  bootstrap-retrain yapar (bkz. `predict.py:main`) — bu kontrolü kaldırma,
  aksi halde canlı çalışma `ValueError` ile çöker.

- **Güven kalibrasyonu** (`backend/calibration.py`): `technical` ve `ml`
  bileşenlerinin ham `confidence`'ı gerçek isabetle örtüşmüyordu (bkz.
  yukarıdaki backtest bulgusu) — `predict.py` artık bunları
  `ensemble.combine()`'a vermeden önce isotonic regression ile kalibre
  ediyor (yön asla değişmez, sadece güven/skor büyüklüğü düzeltilir).
  Kalibratör `retrain.py` tarafından her gün, `compute_rebalance()`'a
  benzer şekilde ayrı bir train/test replay'iyle yeniden fit edilip
  `models/calibration.joblib`'e kaydedilir (mevcut kalibratörlerle
  birleştirilir, bir bileşen o gün yeterli örnek bulamazsa öncekini
  silmez). İlk fit'te ham isotonic, seyrek yüksek-güven kuyruğunda birkaç
  örnekle "%100 isabet" gibi sahte sonuçlar üretti — bu yüzden `fit()`
  ham örnekler yerine en az `MIN_BIN_COUNT` örnek içeren binlerin
  ağırlıklı ortalaması üzerinde çalışır. `whale`/`news`/`orderbook`
  kalibre edilmiyor (backtest edilemedikleri için fit edecek veri yok) —
  `calibration.apply()` kalibratörü olmayan bir bileşen için no-op'tur.
  **Beklenen sonuç önemli**: mevcut veriyle `ml`'in kalibre edilmiş güveni
  neredeyse her zaman 0'a düşüyor (ham güveni gerçek isabetle hiç
  örtüşmüyor) — yani `ml` fiilen abstain eder hale geldi, `technical` de
  ciddi bastırılmış durumda. Bu "sistem bozuldu" değil, kalibrasyonun
  yapması gereken şey; ama genel işlem sıklığının belirgin şekilde
  düşmesini beklenmedik bir regresyon sanma. **`daily_retrain.yml`
  `backend/models/calibration.joblib`'i de commit'lemeli** (yalnızca
  `xrp_model.joblib`'i commit'leyip calibration.joblib'i unutan bir sürümü
  vardı — retrain.py günlük yeniden fit ediyordu ama runner kapanınca
  sessizce siliniyordu, "günlük kendi kendini güncelleme" hiç işlemiyordu).
- `daily_report.py` yeni bir tablo kullanmaz — dünkü 18:00'deki portföy
  durumunu `trades` tablosunu geriye doğru "replay" ederek (o zamandan
  önceki/o ana en yakın işlemin `cash_after`/`xrp_after`'i), dünkü XRP
  fiyatını da `predictions` tablosundaki en yakın çözülmüş tahminin
  `price_at_resolution`'ından yeniden inşa eder. Gmail App Password ile
  `smtplib` üzerinden gönderir (ek pip bağımlılığı yok).

- **Altı bağımsız $1000 kağıt-portföy** (`trading.py`): orijinal ensemble
  portföyü (`portfolio_state`/`trades`, id=1, hiç değişmedi) artı beş
  tekil-sinyal stratejisi — sadece teknik, sadece ML, sadece balina, sadece
  haber, sadece Claude (`trading.STRATEGIES`, `strategy_portfolios`/
  `strategy_trades` tablolarında `strategy` kolonuyla anahtarlı;
  `orderbook`'un kendi stratejisi yok, kullanıcı sadece bu beşini istedi).
  Hepsi **aynı** `compute_rebalance()`/`maybe_trade()`
  mantığından geçiyor — `maybe_trade(db, strategy, ...)` sadece hangi
  tabloya okuyup/yazacağını seçiyor, karar mantığı tekrarlanmıyor.
  `predict.py` her 15 dakikada 6 kez `maybe_trade()` çağırır (ensemble +
  5 tekil, biri başarısız olursa diğerlerini engellemez). `technical`/`ml`
  stratejileri zaten kalibre edilmiş güveni kullanır (calibration.py önce
  çalışır); `claude` calibration.py'den geçmez (bkz. yukarıdaki `claude`
  bileşeni notu — backtest edilemediği için kalibre edecek veri yok).
  **Geçmişe dönük başlangıç**: `backend/backfill_strategy_portfolios.py`
  (yeni `Backfill Strategy Portfolios` workflow'u, `workflow_dispatch`,
  cron yok) `predictions` tablosundaki tüm geçmiş satırları kronolojik
  sırayla `compute_rebalance()`'tan geçirip stratejileri sıfır yerine
  "gerçekte ne yapmış olacaklardı" durumuyla başlatır — idempotent (o
  stratejinin `strategy_trades` kayıtlarını silip yeniden yazar), tekrar
  çalıştırmak güvenli (`claude` gibi geçmişi kısa/hiç olmayan bir strateji
  için bu no-op'a yakın olabilir, sorun değil). **Sıralama önemli**: şema
  migration'ı önce uygulanmalı, `predict.py` canlı işlem yapmaya
  başlamadan önce (ya da hemen sonra — idempotent olduğu için kritik
  değil) backfill workflow'u manuel tetiklenmeli, yoksa backfill canlı
  birkaç işlemin üzerine yazar (zararsız ama gereksiz). Frontend'de "6
  Model Karşılaştırması" tek bir sayfa (`app.js:renderStrategyPanels`) —
  CSS grid (`grid-template-columns: repeat(auto-fit, minmax(200px,1fr))`)
  kullanır, kart listesi yatay kaydırma DEĞİL: geniş tarayıcı
  penceresinde 6 panel otomatik yan yana sığar (minmax genişliği
  `claude` eklenirken 240px'ten 200px'e düşürüldü), dar (telefon) ekranda
  tek sütuna düşer, hiçbir zaman cursor'la yatay kaydırma gerekmez (önceki
  `strategy-cards-scroll` tasarımı kullanıcı "kaydırmayı sevmiyorum, hepsini
  aynı anda görmek istiyorum" diye reddetti). Her panel kendi güncel
  tahminini, isabet pasta grafiğini, portföy değeri/getirisini VE kendi son
  10 işlemini + son 10 tahmin geçmişini (ayrı iki tabloyla,
  `renderStrategyTradesTable`/`renderStrategyHistoryTable`) gösterir —
  eskiden sayfanın altında tek bir paylaşılan "İşlem Geçmişi"/"Geçmiş
  Tahminler" tablosu vardı (sadece ensemble'ı yansıtıyordu), kullanıcı
  bunun "pratik olmadığını" belirtip her stratejinin kendi geçmişini
  görmek istedi, o yüzden kaldırıldı. Bu iki tablo panel içinde ayrı,
  kutulu iki blok (`.strategy-history-block`, `data-block="trades"`/
  `"predictions"`) — her birinin sağ üst köşesinde kendi aç/kapa düğmesi
  var (`setupHistoryToggles`, `#strategy-panels` üzerinde event
  delegation, `.collapsed` class'ı `.table-wrap`'i gizler). Amaç: bir
  stratejinin işlem/tahmin geçmişi diğerlerinden çok kısaysa kullanıcı o
  bloğu kapatıp panelleri görsel olarak hizalayabiliyor — otomatik değil,
  kullanıcının kendi seçimi. Panel shell'i bir kere kurulduğu
  (`buildStrategyPanelsShell`) ve her yenilemede sadece `tbody` içerikleri
  değiştiği için collapse durumu 30sn'lik otomatik yenilemeler arasında
  korunur (tam sayfa yenilemesinde sıfırlanır, kalıcı değil). Hangi
  `predictions` kolonlarının okunacağı `app.js:STRATEGY_CONFIG`'te tanımlı,
  yeni bir strateji eklemek istersen önce oraya bir giriş eklemen yeterli.
  `daily_report.py`'daki günlük mail de aynı 6 portföyü (ensemble + 5
  tekil, isabet + gerçek — komisyon dahil — portföy getirisi yan yana)
  `_strategy_table()` ile ayrı bir tabloda gösterir; ikisi de aynı
  `trading.get_portfolio_state(db, strategy)`/`strategy_trades`
  verisinden besleniyor, birbirinden bağımsız hesap yapmıyor.
  **Panel kâr/zarar rozeti** (`.strategy-portfolio .direction`, sağ üstteki
  yeşil/kırmızı `%`/`$` etiketi): tıklanınca tüm panellerde aynı anda
  dolar ↔ yüzde arasında geçiş yapar (`setupPnlToggle`, tek global
  `showPnlInDollars` bayrağı — panellerin karşılaştırılabilir kalması
  için hepsi birlikte döner, tek tek değil). Değer, sabit $1000 taban
  yerine **seçili tarih aralığının** (24s/7g/30g/tümü, aynı range-buttons)
  gerçek getirisini gösterir: `loadRangePnlBoundary()` aralık başlangıcına
  en yakın-önceki trade'in `cash_after`/`xrp_after`'ini
  (`strategy_trades`/`trades`'e `created_at=lte.<sınır>` ile) ve o
  zamanki XRP fiyatını (Binance `data-api.binance.vision/api/v3/klines`,
  15m mum, tek noktalık yaklaşım) çekip aralık-başı portföy değerini
  yeniden kurar; `computeRangePnl()` güncel değerle farkını alır. "Tümü"
  hâlâ sabit $1000 tabanını kullanır (boundary sorgusuna gerek yok). Bu
  sorgular sadece aralık butonuna tıklanınca (ve sayfa açılışında
  varsayılan 7g için) bir kez çalışır, 3 saniyelik canlı-fiyat
  pollinginde tekrarlanmaz (`RANGE_PNL_CACHE`, `days` alanı seçili
  aralıkla eşleşmiyorsa/henüz yüklenmediyse sessizce sabit-$1000 tabana
  düşer — kırık bir şey göstermek yerine).

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
