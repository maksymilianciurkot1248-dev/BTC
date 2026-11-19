# Plan: mentor biznesowy z aktualnymi pomysłami z internetu i TikToka

Stan na 19.11.2026. Dziś mentor (`mentor.html`, rola `mentor` w `btc_ai.py`) rozmawia na podstawie wiedzy modelu i profilu użytkownika. Nie widzi sieci, więc nie zna bieżących trendów.
Cel: żeby sam znajdował aktualne pomysły, oceniał je pod Twój profil i region (między Dębicą a Rzeszowem) i pilnował, żebyś testował je małym kosztem.

## 1. Co jest możliwe, a co nie (sprawdzone założenia)

| Źródło | Można? | Uwagi |
|---|---|---|
| Wyszukiwanie w sieci | tak | Narzędzie wyszukiwania po stronie API Claude: model sam szuka i cytuje źródła, nasz serwer nie scrapuje. Płatne za wyszukanie; nazwę narzędzia i cennik sprawdzę w dokumentacji API przed wdrożeniem |
| TikTok, bezpośrednio | nie polecam | Brak otwartego API do trendów dla zwykłego konta (API badawcze jest ograniczone do uprawnionych badaczy). Scraping łamie regulamin, jest blokowany i psuje się co kilka tygodni |
| TikTok, pośrednio | tak | Artykuły i zestawienia o trendach TikToka znajdowane wyszukiwaniem w sieci; publiczne strony trendów TikTok Creative Center (do sprawdzenia, czy regulamin pozwala na automatyczny odczyt); Ty wklejasz link lub opis filmu, a mentor ocenia pomysł |
| YouTube, Reddit, Google Trends | częściowo | Oficjalne API (YouTube Data API, Reddit API) wymagają kluczy i mają limity. Google Trends nie ma stabilnego oficjalnego API dla wszystkich, więc zaczynam od wyszukiwania w sieci |
| OLX, Allegro Lokalnie | nie automatycznie | Brak otwartego API ofert; zostają linki wyszukiwań ze strony Produkty |

Zasada: nie budujemy niczego, co łamie regulamin serwisu albo wymaga ukrywania, że to robot.

## 2. Etapy

### Etap A: przegląd rynku na życzenie (ZROBIONE 19.11.2026, niesprawdzone z prawdziwym kluczem)
- Przycisk „Co jest teraz na topie” na stronie Mentor. Mentor szuka w sieci, a odpowiedź ma: pomysł, dlaczego teraz, źródło z linkiem i datą, kapitał, ocenę dla Twojego profilu i regionu, ryzyko.
- Wymóg w prompcie: każdy trend z linkiem i datą publikacji; bez źródła pomysł jest oznaczony „bez potwierdzenia”; trendy starsze niż 90 dni oznaczone jako stare.
- Limity: najwyżej N wyszukiwań na dobę (`MENTOR_MAX_SEARCH_PER_DAY` w `ai.env`), żeby koszt był przewidywalny.
- Gotowe, gdy: odpowiedź zawiera co najmniej 3 pomysły z działającymi linkami, a test sprawdza limit i format źródeł.

### Etap B: cotygodniowy raport (1 sesja)
- Usługa `btc-mentor.timer` raz w tygodniu (np. poniedziałek rano) robi przegląd z etapu A dla zapisanego profilu i zapisuje raport w `mentor_reports.json`; strona pokazuje ostatnie raporty.
- Jedno krótkie powiadomienie na Telegram z 3 najlepszymi pomysłami (zgodnie z zasadą: bez zalewu wiadomości).
- Profil musi leżeć na serwerze (dziś jest tylko w przeglądarce), więc strona zapisze go do `mentor_profile.json` przez API.

### Etap C: lista pomysłów i uczenie się z wyników (1–2 sesje)
- Każdy pomysł dostaje status: do sprawdzenia, testuję, odrzucony, działa. Do pomysłu w teście wpisujesz wydatki i przychody.
- Mentor widzi tę historię, więc nie proponuje drugi raz tego, co odrzuciłeś, i podpowiada, co skalować. Licznik: łączny wynik z pomysłów w złotych, uczciwie z kosztami.
- Reguła zapisana z góry: pomysł, który po zaplanowanym teście (np. 4 tygodnie, budżet X zł) nie dał sprzedaży, mentor oznacza jako nieudany i nie proponuje wariantu bez nowego argumentu.

### Etap D: sprawdzenie popytu lokalnego (1 sesja)
- Połączenie ze stroną Produkty: dla pomysłu „handel produktem” mentor podaje frazy do wyszukania i kryteria (ile ofert, rozrzut cen, jak długo wiszą). Wyniki wpisujesz ręcznie albo wklejasz; kalkulator liczy marżę.
- Gotowe, gdy: z pomysłu da się jednym kliknięciem przejść do kalkulatora z wypełnioną frazą.

### Etap E (opcjonalny, po 2 miesiącach): oficjalne API
- YouTube Data API i Reddit API jako dodatkowe źródła, jeśli wyszukiwanie w sieci okaże się za płytkie. Klucze wpisujesz Ty.

## 3. Filtry jakości (obowiązują od etapu A)
Każdy pomysł musi przejść i być opisany:
1. Legalność i podatki w Polsce (limit działalności nieewidencjonowanej, kiedy CEIDG i VAT).
2. Kapitał na start i maksymalna strata, jeśli się nie uda.
3. Czas do pierwszej sprzedaży i czas pracy w tygodniu.
4. Popyt lokalny w promieniu 40 km albo możliwość sprzedaży online.
5. Konkurencja i sezonowość.
6. Odrzucamy: schematy „szybkiego zarobku”, piramidy, zakup drogich kursów, dropshipping oparty na obietnicach z filmów bez liczb, kryptowaluty z dźwignią, wszystko wymagające zadłużenia.

## 4. Ryzyka i ograniczenia
- Trendy z TikToka bywają przereklamowane i wygasają po kilku tygodniach. Mentor ma podawać wiek trendu i wskazywać, że popularność filmu nie znaczy popytu na sprzedaż.
- Model może zacytować stronę błędnie. Dlatego linki są obowiązkowe, a decyzje wydające pieniądze sprawdzasz w źródle.
- Koszt API rośnie z liczbą wyszukiwań. Limit dobowy i tygodniowy raport trzymają go w ryzach; przed wdrożeniem podam szacunek z aktualnego cennika.
- Mentor nie zna Twojego konta ani bota i nie dostanie dostępu do kluczy.
- Nielegalnych pomysłów (fałszowanie dokumentów, podróbki, oszustwa) mentor nie proponuje i nie rozwija.

## 5. Decyzje po Twojej stronie
1. Miesięczny budżet na API mentora (określa limit wyszukiwań).
2. Czy raport tygodniowy ma iść na Telegram.
3. Kategorie, które Cię interesują lub wykluczasz (np. elektronika tak, ubrania nie).
4. Klucz `ANTHROPIC_API_KEY` w `ai.env` (jeśli jeszcze nie wpisany), wpisujesz sam.

## 6. Kolejność
A → B → C → D. Etap A daje użyteczny wynik najszybciej i przy okazji pokazuje, czy wyszukiwanie w sieci jest wystarczająco dobre, żeby w ogóle budować B–E.
