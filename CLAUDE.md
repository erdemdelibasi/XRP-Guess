# XRP Tahmin Paneli

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
- Yön sinyali beş bağımsız bileşenin (`ensemble.COMPONENTS`) ağırlıklı
  ortalamasıdır: teknik indikatör (`indicators.py`, BTC/ETH lead-lag +
  taker buy ratio dahil), ML model (`ml_model.py`, aynı taker buy ratio bir
  FEATURE_COLUMNS girdisi olarak da kullanılır), XRPL balina/borsa akışı
  (`whale_signal.py`, XRPSCAN public API), haber/düzenleyici sentiment
  (`news_signal.py`, Google News RSS — CryptoPanic denendi ama ücretsiz
  katmanını kaldırmış, $50/hafta'dan başlıyor, o yüzden vazgeçildi), ve
  emir defteri dengesizliği (`orderbook_signal.py`, Binance `/api/v3/depth`).
  Hepsi anahtar gerektirmez. **`orderbook` (ve `whale`/`news`) canlı-only —
  geçmiş arşivi yok, bu yüzden `backend/backtest.py` sadece technical+ML'i
  test edebilir**, bu sınırlama backtest raporunda açıkça belirtilir.
  Ağırlıklar `retrain.py` tarafından günlük olarak son 14 günlük başarı
  oranına göre güncellenir (`model_state` tablosu). `ensemble.recompute_weights`
  her bileşeni **bağımsız** olarak günceller — bir bileşenin (ör. news)
  henüz yeterli geçmişi yoksa sadece o bileşen varsayılan ağırlıkta kalır,
  diğerlerinin kendi aralarında ayarlanmasını engellemez. `whale`/`news`/
  `orderbook` sık sık "sessiz" (confidence=0) kalır — bu bir hata değil,
  `retrain.py` bu satırları isabet oranına dahil etmiyor (abstention). Yeni
  bir bileşen eklemek istersen `ensemble.COMPONENTS`+`COLUMN_PREFIX`'e
  ekleyip `predictions` tablosuna aynı 5-kolonluk örüntüyü
  (`{prefix}_direction/confidence/pct_change/price/correct` + `weight_{c}`)
  uygulaman yeterli — `retrain.py`/`daily_report.py` tamamen bu listeler
  üzerinden döngü kurduğu için başka kod değişikliği gerekmez.
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
  düşmesini beklenmedik bir regresyon sanma.
- `daily_report.py` yeni bir tablo kullanmaz — dünkü 18:00'deki portföy
  durumunu `trades` tablosunu geriye doğru "replay" ederek (o zamandan
  önceki/o ana en yakın işlemin `cash_after`/`xrp_after`'i), dünkü XRP
  fiyatını da `predictions` tablosundaki en yakın çözülmüş tahminin
  `price_at_resolution`'ından yeniden inşa eder. Gmail App Password ile
  `smtplib` üzerinden gönderir (ek pip bağımlılığı yok).

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
