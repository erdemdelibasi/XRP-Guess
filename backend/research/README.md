# Araştırma tezgâhı

Canlı sistemden **tamamen ayrık**. Buradaki hiçbir dosya `predict.py`,
`retrain.py` ya da herhangi bir workflow tarafından çağrılmaz; hiçbiri
Supabase'e yazmaz. Amacı tek: canlı sisteme dokunmadan önce bir hipotezi
ölçüp elemek.

## Neden var

2026-09-07'de şu ölçüldü: 15 dakikalık ufukta XRP'nin ortalama hareketi
%0,219, gidiş-dönüş komisyonu ise %0,20. Yani **başabaş için gereken yön
isabeti %95,6** — modelin kalitesiyle ilgisi olmayan, aritmetik bir duvar.
Bu, "modeli iyileştirelim" sorusunun yanlış soru olduğunu gösterdi ve
sorunun kendisini değiştirme ihtiyacını doğurdu. Tezgâh o yüzden var.

## Disiplin

Projenin defalarca öğrendiği kural burada da geçerli (`REBALANCE_THRESHOLD`,
`CONFIDENCE_FOR_MAX_ALLOCATION`, `momentum_trading` derslerine bak): **aynı
pencerede seçilip aynı pencerede puanlanan bir sayı hiçbir şey ifade etmez.**

- Pencere zamana göre ikiye bölünür. Parametre **yalnızca ilk yarıda**
  seçilir, o **tek** konfigürasyon ikinci yarıda bir kez çalıştırılır.
  Basılan diğer her şey şeffaflık içindir, sonradan seçim yapmak için değil.
- Panel **delist/rename olmuş sembolleri içerir** (MATIC→POL, EOS, LUNA,
  FTT, WAVES...). Bunları dışarıda bırakmak hayatta-kalma yanlılığıdır:
  kaybedenler örneklemden silinir ve her backtest olduğundan iyi görünür.
  Serisi biten bir coin son fiyatından zorla kapatılır.
- Sonuç her zaman **üç maliyet varsayımıyla** basılır: taker (%0,10),
  maker (%0,02), komisyonsuz. Komisyonsuz sütun "sinyal var mı", taker
  sütunu "alınabilir mi" sorusunu ayırır.

## Dosyalar

| Dosya | Ne yapar |
|---|---|
| `panel.py` | 2019'dan bugüne ~350 USDT paritesi için günlük kapanış paneli kurar, `panel.json`'a önbelleğe alır (~35 dk, gitignore'da) |
| `xsec.py` | Kesitsel testler. `ic` / `deciles` / `book` modları |
| `horizon.py` | Aynı technical+ML sinyalini 15dk yerine 1h / 4h / 1d mumlarda yürüyen-ileri test eder |

```bash
cd backend/research
python panel.py            # bir kez, veriyi kurar
python xsec.py ic          # IC'nin egitim->test isaret tutarliligi
python xsec.py deciles     # desil basina ortalama vs medyan getiri
python xsec.py book        # portfoy kurulumlari, egitimde sec / testte kosur
python horizon.py 1d       # yon tahmini, gunluk ufukta (1h / 4h de var)
```

## Şimdiye kadar elenen hipotezler

**Hiçbiri işe yaramadı.** Kayıt tutuluyor ki aynı yollar tekrar denenmesin.

### 1. Yön tahmini — ufku uzatmak (`horizon.py`)

Aynı technical+ML sinyali, sadece mum aralığı değişerek:

| Ufuk | Test adımı | Yön isabeti | Başabaş eşiği | Fark | Brier |
|---|---|---|---|---|---|
| 15 dk | 5799 | %50,0 | %95,7 | −45,7p | negatif BSS |
| 1 saat | 3599 | %50,0 | %76,2 | −26,2p | 0,2586 |
| 4 saat | 2399 | %51,9 | %61,1 | −9,2p | 0,2506 |
| 1 gün | 899 | %52,1 | %53,7 | −1,7p | 0,2565 |

Komisyon açığı monoton kapanıyor ama **beceri hiçbir ufukta ortaya
çıkmıyor** — en iyi z = +1,86, hiçbiri anlamlı değil, Brier skorlarının
hepsi 0,25'in üstünde (yani "%50/%50" demekten kötü).

1 günlük pencerede portföy +%26,9 yaptı, al-ve-tut +%128,6. Üstelik o
+%26,9'un tamamı **899 günün 3'üne** yaslanıyor (en iyi 3 adım çıkarılınca
−%14,4) ve pencerenin ikinci yarısının tamamı nakitte geçmiş. Yarı yarıya
bölünce zamanlama katkısı **tam olarak sıfır**: aynı oranda XRP tutan
pasif bir portföyden ayırt edilemiyor.

### 2. Funding carry (delta-nötr)

XRP perp funding'i son 166 günde yıllık **%0,4**. 2021'de zengindi, artık
değil. Ölçüm `fapi.binance.com/fapi/v1/fundingRate` ile yapıldı.

### 3. Kesitsel momentum (`xsec.py book`, ters işaretle)

Eğitim yarısında seçilen en iyi konfigürasyon (lookback=14g, tut=30g, k=5)
yıllık **+%123**, IR +0,93. Aynı konfigürasyon test yarısında yıllık
**−%34,3**, IR −0,60. Klasik aşırı-uydurma.

### 4. Kesitsel reversal — sinyal gerçek, para değil

Bu en ilginç olanı, çünkü **sinyal gerçekten var**:

`xsec.py ic` — test yarısında 45 konfigürasyonun **44'ünde IC negatif**,
30'unda t < −2, en güçlüsü **t = −6,5** (467 gözlem). İşaret eğitim→test
%89 korunuyor. Bu çoklu-test artefaktı değil; Bonferroni eşiği |t|>3,3.

Ama beş standart portföy kurulumunun hiçbiri bunu paraya çeviremiyor
(hepsi out-of-sample):

| Kurulum | Komisyonsuz | Maker | Taker |
|---|---|---|---|
| `bottom_k` | — | — | −%13,3 |
| `rank_ls` | −%9,4 | −%10,7 | −%16,1 |
| `rank_lo` | +%2,2 | +%1,7 | −%0,2 |
| `decile_ls` | **+%4,6** (t=+0,38) | +%1,7 | −%10,0 |
| `decile_lo` | −%3,8 | −%5,2 | −%11,1 |

En iyi hücre **komisyon sıfır alınsa bile** t=+0,38 — sıfırdan ayırt
edilemez. Komisyon tartışmasına gelmeden bitiyor.

**Sebebi `xsec.py deciles` ile ölçüldü**: ilişki doğrusal değil, U şeklinde
(bu yüzden doğrusal sıralama ağırlığı hiçbir şey yakalamıyor). Asıl mesele
şu — desil 10'un (en çok yükselenler) ortalama getirisi −%0,368 ama medyanı
−%1,607, ve %5,23'ü 3 günde +%20 üstü yapıyor.

**IC bir sıralama istatistiğidir**: bir coinin 300. sıradan 299.'ya
düşmesiyle 10x yapmasını aynı sayar. Dolarlar saymaz. Kazananlara karşı
pozisyon almak kriptonun şişman sağ kuyruğuna sistematik short olmak
demek. Desil 1−10 farkı medyanda +%1,063 iken ortalamada sadece +%0,271 —
avantajın çoğunu kuyruk yiyor.

> Buradaki genel ders: **anlamlı bir IC, alınabilir bir avantaj demek
> değildir.** Bir sinyalin istatistiksel varlığı ile paraya çevrilebilirliği
> ayrı iki sorudur ve ikincisi her zaman ayrıca ölçülmelidir.

## Kapsam uyarısı

Yukarıdakiler "hiçbir şey işe yaramaz" demek değil. **Günlük OHLCV verisiyle,
bu kurulum ailesinde, işe yaramıyor** demek. Test edilmemiş ve bu tezgâhla
test de edilemeyecek olanlar: piyasa yapıcılık (yönü tahmin etmek yerine
spread kazanmak), emir defteri mikroyapısı, komisyon kademesi avantajları.
Bunlar tahmin değil altyapı oyunlarıdır.
