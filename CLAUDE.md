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

Kullanıcının kendi bilgisayarı (Windows Task Scheduler -- GitHub Actions DEĞİL,
bkz. aşağıdaki Kanal Finans notu)
  -> backend/run_kanal_finans.ps1 -> kanal_finans.py   her 15 dk, ensemble'dan bağımsız
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
- **Bir tahmin, hedef anındaki gerçek fiyata göre çözülür** — çözümleyicinin
  çalıştığı andaki canlı fiyata göre DEĞİL (`predict.py:price_at_target`,
  hedef anda kapanan 1 dakikalık mumun kapanışını çeker; mum çekilemezse
  canlı fiyata düşer ki satır yine de çözülsün). Bu ayrım kritik: 2026-09-03'e
  kadar `resolve_due_predictions` canlı fiyatı kullanıyordu ve koşular çeyrek
  saat işaretinden ~1 dk sonra düştüğü için her tahmin 10-30 dk geç
  puanlanıyordu; GitHub Actions cron'u geciktirdiğinde (doğrulanmış 2sa56dk'lik
  bir boşluk) 15 dakikalık bir tahmin ~3 saatlik harekete göre puanlandı.
  Ölçüldü: 240 satırın 80'i (%33.5) yanlış puanlanmıştı, kayıtlı isabet %51.5
  iken gerçek %46.1. Geçmiş satırlar tek seferlik bir script'le yeniden
  çözüldü. **Bu veri `retrain.py`'nin rolling accuracy'sini ve oradan ensemble
  ağırlıklarını besliyor** — yani ölçüm hatası doğrudan öğrenme döngüsünü
  bozuyordu. Buraya tekrar canlı fiyat koyma.
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
  civarı. **Fable 5.1'e yükseltmek 2026-09-03'te değerlendirilip reddedildi**:
  $10/$50 per MTok ile Sonnet 5'in ($2/$10) tam 5 katı, yani ~$25-30/ay — ve
  bu görev `effort="low"` ile 2 alanlı bir JSON sınıflandırması, Anthropic'in
  kendi rehberi sınıflandırma/yüksek-hacim rotalarının model gücüne zayıf
  tepki verdiğini söylüyor. Ampirik olarak da darboğaz model değil: `claude`
  6 bileşenin en kötüsü ve projenin backtest'leri 15 dakikalık XRP yönünde
  ~%50 tavanı gösteriyor. Daha pahalı model gürültü tabanını yenmez.
  **Prompt'taki bir hata 2026-09-03'te düzeltildi**: sistem prompt'u "do not
  just restate the technical signal's direction" diyordu — niyeti körü körüne
  tekrarlamayı önlemekti ama modeli *ayrışmaya* itiyordu, üstelik `technical`
  ölçülen avantajı olan tek bileşen. Canlı veri bununla tutarlıydı: ilk 73
  çözülmüş tahminde `claude`, `technical` ile sadece %48 hemfikirdi, 73'ün
  56'sında DOWN dedi ve %37 isabet tutturdu — yani bilgisiz değil, ters
  korelasyonluydu. Küçük örneklemde bir hipotez, kanıtlanmış bir neden değil;
  birkaç yüz satır daha birikince yön dağılımına ve isabete tekrar bak.
  Hepsi (`claude` dahil) API/parse hatasında sessizce nötre düşer,
  bir bileşenin hıçkırığı tahmin döngüsünü hiç bloklamaz. **`orderbook` (ve
  `whale`/`news`/`claude`) canlı-only — geçmiş arşivi yok (Claude'u ucuza
  geriye test etmenin bir yolu da yok, paralı bir LLM çağrısını geçmişe karşı
  tekrar oynatmak pahalı olurdu), bu yüzden `backend/backtest.py` sadece
  technical+ML'i test edebilir**, bu sınırlama backtest raporunda açıkça
  belirtilir.
  **Bileşenler LOG-ODDS uzayında havuzlanır, güvenle-ağırlıklandırılmaz**
  (`ensemble.combine`). Bu, 2026-09-03'te bulunan sistemik bir tasarım
  hatasının düzeltmesidir: katkı `ağırlık × güven`di ama güven ölçekleri
  karşılaştırılabilir değildi — `calibration.py` sadece `technical`/`ml`'e
  uygulanıp onları sert şekilde büzerken, `whale`/`news`/`orderbook`/`claude`
  5-10 kat büyük ham güvenleriyle kalıyordu. 242 canlı tahminde ölçülen
  gerçek etki: whale %26.5, news %22.3, claude %18.1, orderbook %15.6,
  ml %11.5 ve **technical sadece %6.0** — yani tek avantajlı bileşen (%57.4)
  en kısık sesli, en kötüsü (whale, %42.6) en gürültülüydü. Sadece
  ağırlıkları düzeltmek bunu ONARMAZ, çünkü baskın çarpan güvendi.
  Log-odds havuzlaması bunu yapısal olarak çözer: her bileşen tek bir ölçeğe,
  P(doğru)'ya girer ve bir bileşenin kanıtlanmış güvenilirliği **zaten onun
  ağırlığıdır** — bu yüzden ayrı bir isabet-bazlı ağırlık şemasına gerek
  kalmadı, `recompute_weights` kaldırıldı; yerine sadece gösterim için
  `influence_weights` var. %50 ve altındaki bileşen tam olarak sıfır katkı
  verir, yani **susturulur**.
  Ölçüm (yürüyen-ileri, örnekleme dışı, n=163): bu kural **%54.0**, eski
  güvenle-ağırlıklı oy %41.1, tek başına `technical` %55.2.
  **Denenip reddedilen iki varyant**: (1) %50 altındaki bileşeni susturmak
  yerine TERSİNE okumak (%55.8 — 1.8 puan, bu örneklemde tamamen gürültünün
  içinde; kısa süre canlıda kaldı, sonra `ALLOW_INVERSION=False` ile
  kapatıldı, gerekçe o sabitin yanında yazıyor). Asıl kazanç (%41→%54)
  ters çevirmekten değil, her bileşeni tek bir P(doğru) ölçeğine
  koymaktan geliyor — ters çevirme üstüne ölçülebilir bir şey koymuyor,
  üstelik "altı bileşen de UP derken harman DOWN yazıyor" gibi kullanıcıya
  savunulamayan bir davranış üretiyor. Birkaç yüz satır daha birikip bir
  bileşen hâlâ net %50 altındaysa geri açıp yeniden ölç.
  (2) Katkıyı bileşenin beyan ettiği güvenle ölçeklemek (%53.7, daha kötü)
  — beyan edilen güvenlerin bir şey ifade etmediğiyle tutarlı.
  Üç sabit de seçilmedi, ölçüldü: `SHRINK_ALPHA` (30) P(doğru)'yu örneklem
  boyutuna göre 0.5'e çeker, yani ince kanıt otomatik olarak susar ve sistem
  veri biriktikçe kendi kendini düzeltir; `LOGODDS_CAP` (1.5) tek bir sicilin
  havuzu ele geçirmesini önler; `CORRELATION_DAMPING` (0.7) bileşenler
  bağımsız olmadığı için (technical/ml/orderbook hepsi fiyattan türer) naif
  Bayes toplamının kanıtı çift saymasını düzeltir — sönümlemesiz havuz %58.0
  iddia edip %55.6 teslim ediyordu, 0.7'de %55.7 iddia / %55.6 teslim, yani
  kalibre. **Sönümleme yönü/isabeti DEĞİŞTİRMEZ**, sadece güvenin büyüklüğünü,
  yani `trading.py`'nin pozisyon büyüklüğünü — saf bir bahis-boyutu
  düzeltmesidir (maks pozisyona ulaşan oran %18'den %4'e iner, eski davranışa
  yakın kalır). **Canlı geçmiş hâlâ kısa (~3 gün)** ve 20.000 mumluk backtest
  technical+ML'i %50'de gösteriyor — birkaç hafta sonra sabitleri yeniden
  ölç. `model_state.sample_size` bunun için eklendi (isabet oranı tek başına
  6/10 ile 600/1000'i ayırt edemez); migration `supabase/schema.sql`'de.
  Şu an sadece `technical` (%72) ve `ml` (%28) katkı veriyor, diğer dördü
  %50 altında olduğu için susturulmuş durumda — bu bir arıza değil, kuralın
  çalışması. Gösterim ağırlıkları işaretli tutuluyor (`ALLOW_INVERSION` geri
  açılırsa negatif değerler oluşur; `app.js` ve `daily_report.py` onları
  "(ters)" diye etiketler ki en çok yanılan bileşen listenin başında "en
  güvenilir" gibi görünmesin). Ağırlıklar mutlak değerce
  **her zaman** tam %100'e tamamlanır (float toplamı) — ekranda %100 etmiyormuş
  gibi görünüyorsa veri değil gösterim sorunudur: `app.js`'de her ağırlık
  bağımsız `Math.round`'lanıyordu, hem bu yüzden ~1 puan kayabiliyordu hem
  de `orderbook` gösterime hiç dahil değildi. `roundWeightsTo100()`
  (en-büyük-kalan yöntemi) ve `orderbook`'un eklenmesiyle düzeltildi.
  `ensemble.influence_weights`
  her bileşeni **bağımsız** olarak değerlendirir — bir bileşenin (ör. news)
  henüz geçmişi yoksa sadece o dışarıda kalır, diğerlerini engellemez.
  `whale`/`news`/
  `orderbook` sık sık "sessiz" (confidence=0) kalır — bu bir hata değil,
  `retrain.py` bu satırları isabet oranına dahil etmiyor (abstention). Yeni
  bir bileşen eklemek istersen `ensemble.COMPONENTS`+`COLUMN_PREFIX`'e
  ekleyip `predictions` tablosuna aynı 5-kolonluk örüntüyü
  (`{prefix}_direction/confidence/pct_change/price/correct` + `weight_{c}`)
  uygulaman yeterli — `retrain.py`/`daily_report.py` tamamen bu listeler
  üzerinden döngü kurduğu için başka kod değişikliği gerekmez. **Ama
  `model_state` tablosunda geçici bir tutarsızlık penceresi var**:
  `predict.py:get_ensemble_state` `model_state`'teki satırları olduğu
  gibi `DEFAULT_WEIGHTS` üzerine yazıyor, toplamın 1.0 olduğunu
  doğrulamıyor (bu artık sadece gösterimi etkiler — asıl karar
  `reliabilities` üzerinden log-odds ile veriliyor). `claude` eklendiğinde
  (2026-09-02) tam bunun kanıtı
  yaşandı: o sabahki `retrain.py` koşusu henüz sadece eski 5 bileşeni
  biliyordu ve onlar zaten kendi aralarında %100'e tamamlanacak şekilde
  yazılmıştı; `claude` satırı bundan saatler sonra (şema migration'ıyla
  birlikte, varsayılan 0.15 ile) ayrıca eklenince toplam %115'e çıktı —
  Supabase'e curl atıp doğrulandı, "Daily Model Retrain" workflow'u elle
  tetiklenerek düzeltildi (bir sonraki `retrain.py`, `COMPONENTS`'teki
  **tüm** bileşenleri aynı anda `influence_weights`'ten geçirip
  `model_state`'e tutarlı, toplamı-1.0 bir set yazdığı için kendi kendine
  de düzelirdi). **İleride yeni bir bileşen daha eklersen**: eklendiği gün
  ile bir sonraki günlük retrain arasında ağırlık toplamı geçici olarak
  %100'ü aşabilir (ya da varsayılan yüzdesi kadar eksik kalabilir) —
  bu beklenen bir geçiş durumudur, hemen "bug" deyip `get_ensemble_state`
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
  **2026-09-03'te o kontrol yapıldı** (240 canlı tahmin): `whale` GERÇEKTEN
  düzelmiş (UP 66 / DOWN 68, dengeli). **`news` düzelmemişti** — 127 satırın
  121'inde hâlâ UP diyordu. Kelime-sınırı düzeltmesi çalışıyordu; yanlılık
  başka iki yerden geliyordu: (a) skor, penceredeki *tüm* başlıkların anahtar
  kelime vuruşlarının TOPLAMI'ydı, yani sinyal gücü haber *hacmiyle*
  ölçekleniyor ve "surge/rally/bullish" ile doldurulmuş tek bir clickbait
  başlık üç kez sayılıyordu; (b) kripto başlık sözlüğü yapısal olarak
  promosyoneldir — `launch`/`partnership`/`rally` rutin haberde her saat
  geçer, `sued`/`banned`/`fraud`/`hacked` ise sadece gerçek bir olayda. Yani
  bileşen bir sinyal değil, ~%15 ağırlıkla sabit bir UP enjektörüydü. Artık
  **başlık başına tek oy** (anahtar kelime başına değil), toplam eşleşen
  ağırlığa **normalize** (hacim düşer) ve belirgin bir eğim yoksa **abstain**
  ediyor (`MIN_MATCHED_HEADLINES`, `MIN_IMBALANCE`). Bu bileşenin çoğu zaman
  SESSİZ kalması doğru davranıştır, arıza değil. Bu iki eşik ilk tahmindir —
  geçmiş başlık arşivi olmadığı için fit edilemezler, canlı `predictions`
  verisiyle doğrulanmaları gerekir: **yine "düzeldi" demeden önce UP/DOWN
  dağılımına tekrar bak.**
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
  Mailde ayrı bir **Kanal Finans TŞ kartı** da var (`_kanal_finans_card`,
  `kanal_finans_context`). İki farklı soruyu iki farklı sorguyla yanıtlar:
  "bugün ne geldi" `created_at`'e göre (bizim işlediğimiz an — YouTube
  engeli bir gün koşuyu bloklayıp ertesi gün telafi ederse mail onu
  gerçekten geldiği gün raporlar), "XRP'de güncel görüş ne" ise tarihten
  bağımsız olarak **en son XRP mention'ı**. İkincisi şart: kanal her gün
  video atmıyor ve yerel görev makine uykudayken atlanıyor, yani yeni video
  olmayan bir günde portföyün hâlâ üzerinde işlem yaptığı görüş bir önceki
  videonunki. Sadece pencereye bakan bir kart o günlerde "bugün bir şey yok"
  deyip hiçbir bilgi taşımazdı. XRP dışı (BTC/ETH/KRIPTO) bahislerden
  **sadece en son videonunkiler** basılır — ilk backfill koşusu tüm arşivi
  aynı `created_at`'e yazdığı için (ve engel sonrası birikmiş bir gün de
  aynısını yapabilir) filtresiz hali maile 37 satır eski özet dolduruyordu.
  Bölümün tamamı fail-soft: kanal finans tabloları yoksa/Supabase hıçkırırsa
  kart düşer, mailin geri kalanı yine gider.

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
  çalıştırmak güvenli. **Kolon adını strateji adından kurma** —
  `ensemble.COLUMN_PREFIX`'ten al: `technical` tabloda `tech_*` olarak
  duruyor. 2026-09-03'e kadar `replay()` düz `f"{strategy}_direction"`
  kuruyordu, `technical_direction` diye bir kolon olmadığı için her satır
  None-guard'a takılıp atlanıyordu ve technical **sessizce** düz $1000 / 0
  işlemle backfill oluyordu — hiç işlem yapmamış bir strateji gibi görünüyor,
  hata gibi görünmüyordu. Düzeltildikten sonra aynı geçmiş 10 işlem /
  +%0.52 veriyor (`claude` gibi geçmişi kısa/hiç olmayan bir strateji
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
  `daily_report.py`'daki günlük mail de aynı portföyleri (ensemble + 5
  tekil + Kanal Finans TŞ = 7 satır, isabet + gerçek — komisyon dahil —
  portföy getirisi yan yana) `_strategy_table()` ile ayrı bir tabloda
  gösterir; ikisi de aynı
  `trading.get_portfolio_state(db, strategy)`/`strategy_trades`
  verisinden besleniyor, birbirinden bağımsız hesap yapmıyor.
  Kanal Finans o tabloda `trading.STRATEGIES`'in bir üyesi olarak değil,
  `daily_report.REPORT_STRATEGIES`+`KANAL_FINANS` sabitiyle ayrıca ekli
  (kendi tabloları, kendi karar motoru var) — isabet hücresi bilerek boş,
  çünkü 15 dakikalık yön çağrısı üretmiyor; puanlanacak bir şey yok.
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

- **Kanal Finans TŞ** (`backend/kanal_finans.py`, YouTube @KanalFinans /
  Tunç Şatıroğlu): kullanıcının takip ettiği bir piyasa YouTube kanalının
  günlük videolarında XRP/BTC/ETH/kripto hakkında söylediklerini Claude ile
  çıkarıp ayrı bir bilgi akışı olarak gösterir. **`ensemble.COMPONENTS`'e
  eklenmedi ve `predict.py` bu modülü hiç çağırmıyor** — burada bir tahmin
  üretmiyoruz, sadece Tunç Şatıroğlu'nun ne dediğini raporluyoruz. Kanal, RSS
  ile key'siz takip edilir
  (`https://www.youtube.com/feeds/videos.xml?channel_id=UCGBytjbMXiF1nbe6HD7iORQ`
  — channel_id kanalın `canonical` linkinden bir kere çözülüp sabitlendi,
  handle değişse bile ID sabit kalır).

  **Neden GitHub Actions'ta DEĞİL de kullanıcının kendi bilgisayarında
  çalışıyor**: canlıda doğrulandı (2026-09-02) — GitHub Actions'ın Azure IP
  aralığından yapılan transkript istekleri YouTube tarafından `RequestBlocked`
  ile sistematik olarak reddediliyor (iki ayrı manuel koşuda 15 videonun 15'i
  de aynı hatayla başarısız oldu). `api.binance.com` için yaşanan bulut-IP-
  engeli riskinden (bkz. yukarıdaki Binance notu) daha ciddisi — orada
  alternatif bir "vision" host'u işe yaradı, burada YouTube'un önerdiği
  çözüm bir proxy (`_build_api()` hâlâ `WEBSHARE_PROXY_USERNAME`/
  `WEBSHARE_PROXY_PASSWORD` set edilirse `WebshareProxyConfig` üzerinden
  bağlanmayı destekliyor, ama kullanıcı webshare.io'ya kurumsal ağından
  erişemediği için şimdilik kullanılmıyor). **2026-09-03 güncellemesi: "yerel
  makine engellenmiyor" varsayımı artık doğru DEĞİL** — o gün hem 10:15'teki
  zamanlanmış koşu (`QsJ-xe4BJh0`) hem de elle yapılan iki test koşusu
  (`_y4xjtJg5Qg`) kullanıcının kendi makinesinden `IpBlocked` aldı. Yani
  yerele taşımak engeli tamamen çözmedi, sadece azalttı; kalıcı çözüm hâlâ
  bir residential proxy (Webshare) ve o da kurumsal ağ erişimine bağlı.
  Bu yüzden tekrar-deneme geri çekilmesi (aşağıda) kozmetik bir iyileştirme
  değil, engeli kötüleştirmemek için gerekli. — bu yüzden
  `kanal_finans.yml`'deki `schedule:` tetikleyicisi **bilerek kaldırıldı**
  (sadece `workflow_dispatch` kaldı, elle test için), gerçek zamanlama
  `backend/run_kanal_finans.ps1` + Windows Task Scheduler ile kullanıcının
  makinesinde **her 15 dakikada bir** (00:00'dan başlayan günlük tetikleyici,
  15 dk tekrar aralığı, 24 saat süre) çalışıyor. Eskiden günde 4 kezdi
  (10:15/15:00/19:00/23:30); 2026-09-03'te 15 dakikaya çekildi çünkü yeni
  video YOKKEN bir koşunun maliyeti pratikte sıfır: bir RSS GET (~3 KB) +
  bir Supabase SELECT, sonra `return` — transkript yok, Claude çağrısı yok,
  para yok. Kazanç, yeni bir videoyu yakalama gecikmesinin ~6 saatten 15
  dakikaya inmesi. **YouTube'un WebSub/PubSubHubbub push'u değerlendirilip
  reddedildi**: gerçek bir push servisi var (saniyeler içinde bildirim) ama
  callback'in herkese açık bir HTTPS endpoint olması gerekiyor; transkripti
  çekmesi gereken makine ise kurumsal ağın arkasındaki yerel makine, yani
  bildirim Vercel/Supabase'e düşse bile yerel makine yine bir yeri
  yoklamak zorunda — sorgu ortadan kalkmıyor, sadece YouTube'dan Supabase'e
  taşınıyor. Gerçek push için makinede 7/24 açık bir Supabase Realtime
  dinleyicisi (Task Scheduler yerine servis) gerekirdi; uyku/reboot'ta
  ölen, çok daha kırılgan bir parça karşılığında kazanç 15 dakikadan
  saniyelere inmek — günde birkaç video atan bir kanal için değmiyor.
  Görev ayrıca `MultipleInstances=IgnoreNew` (yavaş bir koşu üst üste
  binmesin), `ExecutionTimeLimit=10 dk` (tekrar aralığının altında) ve
  **pilde de çalışacak** şekilde ayarlandı — `DisallowStartIfOnBatteries`
  varsayılan olarak açıktı, yani dizüstü fişten çekiliyken görev HİÇ
  çalışmıyordu; 3 saniyelik bir Python koşusu için bu ayar tüm mekanizmayı
  boşa çıkarıyordu.
  **Bu, projenin "sunucusuz" mimarisinden bilinçli bir sapma** — makine o
  saatlerde kapalıysa/uykudaysa o çalıştırma atlanır, bir sonraki zamanlanmış
  çalıştırmada `main()` zaten idempotent olduğu için otomatik telafi olur.
  `run_kanal_finans.ps1`, `backend/.env`'i (gitignore'da, `.env.example`
  şablonundan elle kopyalanır — GitHub Secrets'taki değerlerle aynı olmalı)
  okuyup ortam değişkeni olarak yükler ve çıktıyı `backend/logs/`'a
  (gitignore'da) tarihli bir dosyaya yazar. **Bu boru hattının iki ucu da
  UTF-8'e zorlanmalı** ve bu kozmetik değil: Windows'ta yönlendirilmiş bir
  stdout varsayılan olarak cp1252'dir, Türkçe bir video başlığını `print`
  etmek `UnicodeEncodeError` ile TÜM koşuyu öldürür. 2026-09-03'te tam bu
  oldu — zamanlanmış görev her koşuda kod 1 ile çıkıyor, log video ortasında
  kesiliyor ve `record_failure()` hiç çalışmadığı için yeni kurulan
  tekrar-deneme geri çekilmesi sessizce hiçbir şey kaydetmiyordu (tablo
  boştu; sebebin migration olduğu sanıldı, değildi). İki ayrı yarısı var:
  `kanal_finans.py` `sys.stdout.reconfigure(encoding="utf-8")` ile yazarken,
  `run_kanal_finans.ps1` `[Console]::OutputEncoding`'i UTF-8 yapar — çünkü
  PowerShell yerel bir programın çıktısını onunla ÇÖZER, cp1252 kalırsa
  UTF-8 baytlar mojibake olur. Biri olmadan diğeri yetmez. Webshare erişimi ileride
  mümkün olursa `kanal_finans.yml`'e `schedule:` geri eklenip yerel görev
  kapatılabilir — dosya bu geçiş için bilerek silinmedi.

  Bir video ancak transkript **ve** Claude çıkarımı ikisi de başarıyla
  tamamlandıktan sonra `kanal_finans_videos`'a yazılır (kripto bahsi hiç
  yoksa bile 0 mention'lı "işlendi" satırı normaldir); herhangi bir adım
  başarısız olursa video hiç yazılmaz ve bir sonraki zamanlanmış çalıştırmada
  otomatik tekrar denenir. **Tekrar denemeler geri çekilmeli**
  (`kanal_finans.py:RETRY_SCHEDULE`, sayaç `kanal_finans_fetch_attempts`
  tablosunda): ilk 3 başarısızlık her koşuda (15 dk) yeniden denenir, sonra
  sırasıyla 1 saat / 4 saat / 12 saat aralıklarla. Bu, 15 dakikalık zamanlama
  değişikliğinin zorunlu eşlikçisidir — geri çekilme olmadan transkripti
  IP-engelli tek bir video günde 96 kez, **zaten bizi reddeden** endpoint'e
  vurulurdu; geçici bir engeli kalıcıya çevirmenin en garanti yolu bu olurdu.
  Kalıcı takılı bir video böylece ~2 deneme/gün'e oturur, yeni bir video ise
  hiç geciktirilmez (kaydı olmadığı için her zaman "due"dur). Bilerek bir
  pes-etme eşiği yok: genişleyen aralık maliyeti zaten sınırlıyor ve video
  eninde sonunda RSS penceresinden düşüyor. Satır video başarıyla
  işlenince silinir (`clear_failures`). Tablonun okunması/yazılması
  **fail-soft**: tablo yoksa (migration uygulanmamışsa) ya da Supabase
  hıçkırırsa koşu ölmez, sadece geri çekilme kaybolur ve eski "her koşuda
  yeniden dene" davranışına düşülür — bu yüzden o durumda log'a **yüksek
  sesli bir WARNING** basılır, çünkü tam olarak önlemek istediğimiz şey odur. Claude'a (`claude-sonnet-5`, aynı
  `claude_signal.py` modeli) **kendi görüşünü değil, konuşmacının
  söylediğini sadakatle özetlemesi** açıkça söyleniyor (`SYSTEM_PROMPT`) —
  `stance` (UP/DOWN/NEUTRAL) Tunç Şatıroğlu'nun tonunu yansıtır, bizim
  tahminimiz değildir; frontend'de bu netleştirilmek için ensemble'ın
  İngilizce "UP"/"DOWN" etiketlerinden bilerek farklı, Türkçe "Olumlu/
  Olumsuz/Nötr" rozetleri kullanılıyor (`app.js:KANAL_FINANS_STANCE_LABELS`)
  — aynı `--up`/`--down` renk paleti, farklı metin. Maliyet günde birkaç
  video × 1 Claude çağrısı — `claude_signal.py`'nin günde 96 çağrısının çok
  altında, önemsiz. Backtest edilemez (canlı-only, geçmişe dönük ucuz bir
  replay yolu yok — `claude_signal.py` ile aynı sınırlama).

- **Kanal Finans TŞ takip-portföyü** (`backend/kanal_finans_trading.py`):
  yedinci $1000 kağıt-portföy — diğer altısı (ensemble + technical/ml/whale/
  news/claude) `trading.compute_rebalance()`'ın güven-skalalı, 15-dakikalık-
  periyot rebalance mantığından geçerken, **bu portföy `compute_rebalance()`'ı
  hiç kullanmaz**. Sebep: Tunç Şatıroğlu sayısal bir güven vermiyor, ikili
  (al/sat/tut) bir yorum veriyor — bu yüzden kendi küçük, olay-tabanlı karar
  fonksiyonu (`decide_on_mention`/`check_stop_loss`) var. **Pozisyon büyüklüğü
  tam giriş/çıkış** (diğer altısının güven-bazlı kısmi pozisyonundan bilinçli
  fark) — kullanıcı bunu net olarak istedi ("bu adamın dediğini yap"), ve
  ölçeklenecek bir güven sayısı zaten yok. `kanal_finans.py`'nin Claude
  çıkarım şeması artık her XRP mention'ı için (diğer varlıklar için hep
  action=HOLD, stop/direnç=0) `action`/`stop_loss_price`/`resistance_price`
  de döndürüyor (0 = "bahsedilmedi" sentinel, `claude_signal.py`'deki gibi
  bu json_schema dialect'i nullable desteklemediği için).
  **Birden fazla seviye verildiğinde hangi ucun alınacağı prompt'ta açıkça
  yazılıdır ve bu ikisi için TERS yönlerdir** — zarar-keste EN YÜKSEK
  (fiyat düşerken oraya önce değer), dirençte EN DÜŞÜK (fiyat yükselirken
  oraya önce değer). Eskiden prompt sadece "daha temkinli / pozisyonu daha
  erken kapatan ucu kullan" diyordu; "temkinli" kelimesi bu iki alan için
  zıt yönleri işaret ettiği için model karıştırdı: Tunç'un "1.37/1.3450
  altına düşerse zarar kes" cümlesinden 1.345'i çıkardı (doğrusu 1.37 —
  gerçek bir pozisyonda ~%1.8 fazla zarar demek). 2026-09-03'te her iki
  alan için yön ve karşı-örnek açıkça yazılarak düzeltildi, aynı cümleyle
  test edilip 1.37/1.41 verdiği doğrulandı; canlıdaki yanlış değer de
  (mention satırı + portföyün izlediği seviye) elle düzeltildi. Yeni bir
  mention
  gelince (`kanal_finans.py:main()`, o anki canlı fiyatla) `apply_mention_decision`
  BUY/SELL uygular; bir sonraki mention'da yeni bir seviye verilmemişse
  **önceki izlenen zarar-kes/direnç seviyesi korunur** (Tunç her videoda
  tekrar etmiyor). **Zarar-kes sürekli izlenir** — sadece yeni video geldiğinde
  değil, `predict.py`'nin her 15 dakikalık döngüsünde de
  (`kanal_finans_trading.maybe_check_stop_loss`, `main()`'in sonunda,
  `trading.maybe_trade` döngüsünden hemen sonra) — bu çağrı sadece Supabase +
  zaten çekilmiş `current_price`'a dokunuyor, **YouTube'a hiç gitmiyor**, o
  yüzden `kanal_finans.py`'nin aksine GitHub Actions'ta sorunsuz çalışır.
  **Direnç seviyesi kasıtlı olarak otomatik satış tetiklemez, sadece bilgi
  amaçlı gösterilir** — canlı veride Tunç'un direnç kırılmasını bazen
  "yükseliş fırsatı/alım" olarak yorumladığı görüldü (`"1.41 direncinin
  geçilmesi bekleniyor, geçilirse alım fırsatı olabilir"`), yani
  direnç=otomatik-sat sabit kuralı bazı durumlarda tam tersini yapardı; sadece
  zarar-kes (destek) seviyesi otomatik SELL tetikler. **Geçmişe dönük
  başlangıç**: `backend/backfill_kanal_finans_portfolio.py`
  (`workflow_dispatch` yok, tek seferlik, elle yerelde çalıştırılır — YouTube'a
  hiç dokunmuyor ama transkript arşivi tutulmuyor, o yüzden zaten var olan
  `kanal_finans_mentions.summary` metninden yapılandırılmış alanları küçük bir
  Claude çağrısıyla geriye dönük çıkarır) `kanal_finans_mentions`'daki XRP
  satırlarını kronolojik sırayla gerçek 15dk Binance mumlarına (`fetch_data.py`)
  karşı replay eder — `backfill_strategy_portfolios.py` ile aynı desen
  (in-memory replay, idempotent, sonunda tek seferde DB'ye yaz).

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
