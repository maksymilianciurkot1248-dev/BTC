# Alfred: osobisty plan dnia i asystent (strona `alfred.html`, w menu „Dziś”)

Stan na 22.11.2026. Cel: strona, z którą rozwijasz się codziennie: widzi Twój kalendarz, zadania, nawyki i cele, rano mówi, co dziś najważniejsze,
wieczorem pomaga podsumować dzień i przez cały czas odpowiada jak doradca (inspiracja: Alfred z Batmana i J.A.R.V.I.S. z Iron Mana, bez teatralności).
Nazwę „Alfred” zmienisz w jednym miejscu: `ALFRED` w `btc_ai.py` i napisy w `alfred.html`. Wersja z 22.11 (wieczór) upraszcza obsługę i łączy funkcje: patrz rozdział 5.

## 1. Co już działa

| Element | Jak | Gdzie |
|---|---|---|
| Powitanie i pasek stanu | pora dnia, następne wydarzenie, zadania na dziś, nawyki, postęp celu zarobkowego | góra strony |
| Poranny briefing 07:00 | model AI z Twoich danych; zapis na stronie i wiadomość na Telegram; bez klucza AI powstaje briefing zwykły z tych samych danych | `btc_alfred.py --morning`, timer `btc-alfred-morning` |
| Kalendarz Google | tylko odczyt przez „tajny adres iCal”, odświeżany co 15 min; oś dnia, wolne okna 8-20 (zmienisz: `ALFRED_DAY_START/END`) | `btc_agenda.py`, timer `btc-agenda` |
| Zadania z priorytetami i szacunkiem czasu | najwyżej 3 priorytety, terminy, zaległe, czas w minutach (domyślnie 30) | strona, dane na serwerze |
| Plan dnia | zadania układane w wolnych oknach kalendarza (priorytety pierwsze, pierwsze pasujące okno, 5 min przerwy), zakładki Dziś i Jutro, zadania, które się nie mieszczą, można jednym kliknięciem przenieść na jutro | `btc_alfred.plan_day`, `GET /api/today` |
| Następny krok | jedno zalecenie „co teraz” z planu: przegląd dnia po 19:00, trwające lub zbliżające się wydarzenie, pierwsze zadanie, przeciążenie dnia, brak planu, odpoczynek | `btc_alfred.next_step` |
| Tryb skupienia | odliczanie czasu zadania w pasku u dołu i w tytule karty; „Zrobione” zamyka zadanie | strona |
| Alerty | bot (wyłącznik ryzyka, brak raportu), kopia zapasowa, kalendarz, klucz AI, zaległe zadania, oferty stojące za długo; na górze tylko pilne | `btc_alfred.alerts` |
| Pieniądze z „Zarabiaj” | zysk w miesiącu i postęp celu, oferty w toku, zamrożone pieniądze, oczekiwany zysk | kafelek w stronie, `btc_deals.py` |
| Nawyki | odhaczanie ostatnich 7 dni, serie | strona, dane na serwerze |
| Cele | z liczbą, jednostką i terminem; cel „Zarobki” liczy się sam z notatnika Zarobki | strona |
| Wieczorny przegląd | trzy pytania (co poszło, czego się nauczyłem, co jutro pierwsze) | strona |
| Przypomnienie wieczorne 20:30 | krótka wiadomość Telegram, tylko gdy jest o czym przypominać | `btc_alfred.py --evening`, timer `btc-alfred-evening` |
| Czat z Alfredem | zna Twój dzień; szybkie polecenia (briefing, plan na jutro, przegląd tygodnia, przegląd dnia); głos (mów i słuchaj); „Zamień rozmowę na zadania” | rola `alfred` i `tasks` w `btc_ai.py` |
| Radar narzędzi | Alfred szuka w internecie nowych narzędzi i pomysłów pod Twój plan, ze źródłami i datami | przycisk w czacie (płatne wyszukiwanie, dobowy limit) |
| Dane | zapis na serwerze (telefon i komputer), konflikty między urządzeniami scalane po id (nic nie ginie), praca na kopii przy braku serwera, kopia zapasowa na Dysk Google | `btc_userdata.py`, `ui.js` (UD.sync), `btc_backup.py` |

Alfred niczego sam nie zmienia: nie edytuje kalendarza, zadań ani nie składa zleceń. Podpowiada, a Ty decydujesz.

## 2. Co znalazłem (narzędzia i pomysły), z oceną

Źródła to wyniki wyszukiwania z 22.11.2026. Tam, gdzie opieram się na czyichś opisach, a nie na dokumentacji, piszę to wprost.

### Użyte teraz
- **Kalendarz Google przez tajny adres iCal.** Brak OAuth, brak projektu w Google Cloud, odczyt tylko do odczytu. Minus: Google nie podaje, jak szybko adres odzwierciedla zmiany. Opisy z innych programów mówią o opóźnieniach od kilku godzin do doby, ale dotyczą subskrypcji w innych aplikacjach, więc dla Twojego przypadku to hipoteza do sprawdzenia: dodaj w kalendarzu wydarzenie i zmierz, po jakim czasie pojawi się w Alfredzie. Źródła: [Google: Sync your calendar with computer programs](https://support.google.com/calendar/answer/37648), [opis częstotliwości odświeżania kanałów Google](https://knowledge.cheqroom.com/helpcenter/how-often-does-google-update-its-calendar-feeds).
- **recurring-ical-events (3.x) + icalendar.** Rozwija powtarzające się wydarzenia, wyjątki i przeniesione terminy do konkretnych dat w oknie czasowym. Sprawdziłem na realistycznym kalendarzu w testach (powtórzenia, EXDATE, przeniesienia, całodniowe, strefy, anulowane). Źródło: [python-recurring-ical-events](https://github.com/tobixen/python-recurring-ical-events).
- **Poranny briefing na Telegramie.** Ten pomysł powtarza się w projektach samodzielnie hostowanych asystentów (OpenJarvis z „morning digest” i głosowym czytaniem, AIDA, OwnPilot). Nie weryfikowałem ich działania ani utrzymania: traktuję je jako źródło pomysłów, nie jako zależność. Źródło przeglądu: [wyniki wyszukiwania: self-hosted AI assistants](https://github.com/open-jarvis/OpenJarvis).
- **Web Speech API (mów i słuchaj w przeglądarce).** Rozpoznawanie mowy: Chrome (z przedrostkiem webkit), Safari 14.1+ i iOS 14.5+ według źródła wtórnego, Firefox tylko za flagą. Dla języka polskiego nie znalazłem potwierdzenia jakości rozpoznawania ani dostępnych głosów: to do sprawdzenia u Ciebie. W Chrome rozpoznawanie, z tego co wiem, korzysta z usług Google (nie sprawdzałem tego w dokumentacji). Funkcja jest dodatkiem: bez obsługi przeglądarki przyciski się nie pokazują. Źródła: [Can I use: Speech Recognition](https://caniuse.com/speech-recognition), [MDN: Web Speech API](https://developer.mozilla.org/docs/Web/API/Web_Speech_API).
- **Planowanie „jeśli-to” (implementation intentions) i małe kroki.** Meta-analiza Gollwitzera i Sheerana z 2006 r. podawana jest jako d = 0,65 (dane z cytowań wtórnych, nie z samego artykułu); w węższych dziedzinach efekty są mniejsze (np. aktywność fizyczna g ≈ 0,31). Wniosek praktyczny, który zastosowałem w prompcie Alfreda: konkretny krok z czasem i miejscem zamiast ogólnego planu, maksymalnie trzy priorytety. Źródło: [Implementation intentions (NCI)](https://cancercontrol.cancer.gov/brp/research/constructs/implementation-intentions).

### Następne (wymagają Twojej decyzji albo większej pracy)
- **Google Calendar API i Google Tasks API przez OAuth.** Dają zapis (Alfred mógłby wstawiać bloki czasu) i szybsze zmiany, a Tasks pozwala pobrać zadania z telefonu. Zakresy tylko do odczytu: `tasks.readonly` ([Google Tasks: scopes](https://developers.google.com/tasks/auth)). Problem: dla aplikacji OAuth w stanie „Testing” autoryzacja wygasa po 7 dniach ([pomoc Google Cloud](https://support.google.com/cloud/answer/10311615)), więc trzeba by opublikować własną aplikację „In production” (ekran ostrzeżenia dla niezweryfikowanej aplikacji; nie sprawdziłem, czy dla zakresów kalendarza wymaga weryfikacji). Dlatego wybrałem adres iCal na start.
- **ntfy (powiadomienia push z własnego serwera).** Publikacja to zwykły POST HTTP na temat, aplikacje na Androida i iOS, serwer można hostować samodzielnie ([dokumentacja ntfy](https://docs.ntfy.sh/publish)). Alternatywa lub dodatek do Telegrama; nie robię, dopóki Telegram Ci wystarcza.
- **PWA z powiadomieniami push.** Na iPhonie powiadomienia web push działają tylko dla aplikacji dodanej do ekranu głównego (iOS 16.4+), wymagają service workera i zgody po stuknięciu ([WebKit: Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)). Dużo pracy przy małej wartości ponad Telegram.
- **Integracja z menedżerem zadań (np. Vikunja przez CalDAV)**, jak w Niles AI: pomysł, nie plan.

## 3. Plan rozwoju (kolejność i kryteria)

| # | Zadanie | Rozm. | Kryterium ukończenia |
|---|---|---|---|
| A1 | Podłączyć kalendarz i sprawdzić opóźnienie nowych wydarzeń | S | zmierzony czas od dodania wydarzenia do pojawienia się w Alfredzie |
| A2 | Tydzień codziennego używania: poranny briefing, wieczorny przegląd | — | po tygodniu zapisz, czego brakowało; to zdecyduje o kolejności poniżej |
| A3 | Pole „jeśli-to” przy zadaniach (kiedy i gdzie to zrobię) | S | zadanie z wyzwalaczem trafia do briefingu |
| A4 | Automatyczny przegląd tygodnia w niedzielę wieczorem (z zadań, nawyków, celów i zarobków) | M | wiadomość Telegram i zapis w dzienniku |
| A5 | OAuth (Kalendarz i Zadania, odczyt) | M | decyzja o publikacji własnej aplikacji OAuth; token nie wygasa po 7 dniach |
| A6 | Bloki czasu proponowane przez Alfreda i zapisywane do kalendarza (po A5, zapis dopiero po Twoim kliknięciu) | M | blok dodany tylko po zatwierdzeniu |
| A7 | Statystyki „plan kontra wykonanie” i związek z notatnikiem Zarobki | M | wykres tygodniowy; wnioski oznaczone jako korelacja, nie przyczyna |

## 4. Ograniczenia i ryzyka (bez ukrywania)
- **Alfred może się mylić.** To model językowy: dobry w porządkowaniu i podpowiadaniu, nie jest wyrocznią. Dane, których nie ma w migawce, są oznaczone jako nieznane. Decyzje finansowe i zdrowotne podejmujesz Ty.
- **Opóźnienia kalendarza.** Adres iCal nie ma gwarancji świeżości (patrz wyżej). Strona pokazuje wiek danych i ostrzega, gdy są starsze niż 6 godzin.
- **Prywatność.** Do API Claude (Twoje konto) trafia migawka: tytuły i miejsca wydarzeń, zadania, nawyki, cele, skrót finansów i bota, ostatnie przeglądy. Bez opisów wydarzeń i uczestników (to treści od osób trzecich, mogłyby zawierać polecenia), bez kluczy i adresu kalendarza. Dane są też na Twoim Dysku Google w kopiach zapasowych. Treści z kalendarza i zadań są w prompcie oznaczone jako dane, nie polecenia, ale żadna taka ochrona nie jest absolutna.
- **Adres kalendarza to sekret.** Kto go ma, czyta cały kalendarz. Leży tylko w `alfred.env` (prawa 640), nie trafia do plików stanu, panelu ani kopii zapasowych. Wyciek: w ustawieniach kalendarza Google zresetuj tajny adres.
- **Koszt AI.** Briefing to jedno zapytanie dziennie: z kontekstem rzędu kilku tysięcy tokenów szacuję je na kilka groszy (około 1 USD miesięcznie według cennika `claude-opus-5-5`, to szacunek bez pomiaru). Radar używa płatnego wyszukiwania i dobowego limitu `MENTOR_MAX_SEARCH_PER_DAY` wspólnego z mentorem i ofertami.
- **Głos.** Jakość polskiego rozpoznawania i głosu zależy od przeglądarki i systemu; nie testowałem jej na prawdziwym urządzeniu.
- **Nie testowałem** na Twoim kalendarzu ani na prawdziwym serwerze: testy używają przykładowego iCal i atrapy modelu. Pierwsze uruchomienie jest testem integracji.

## 5. Jak funkcje współpracują (22.11, wieczór)

Cel zmiany: mniej klikania i żeby pieniądze, plan dnia i rozmowy z doradcą opierały się na tych samych danych.

1. **Oferta → pieniądze → plan dnia.** Na stronie Zarabiaj każdy krok oferty (kupione, wystawione, sprzedane) zapisuje jednym atomowym zapisem ofertę, wpisy w notatniku i zadania w „Dziś”: zakup daje koszt i zadanie „Wystawić” na jutro, wystawienie zadanie „Sprawdzić zainteresowanie” za 5 dni, sprzedaż przychód i prowizję. Cofnięcie kroku sprząta tylko to, co krok dodał sam. Kod: `btc_deals.py`, trasa `POST /api/deal`.
2. **Jedna migawka dnia dla Alfreda i mentora.** Obaj dostają ten sam obraz (profil, kalendarz i wolne okna, zadania, nawyki, finanse, oferty w toku), więc mentor proponuje pomysły dopasowane do wolnych okien i do tego, co już zacząłeś, a Alfred widzi, że masz coś do wystawienia.
3. **Rozmowa → zadania.** Z rozmowy (z Alfredem albo mentorem) model proponuje 1-4 zadania w formacie JSON; serwer sprawdza i normalizuje (tekst do 120 znaków, 10-120 min, termin do 7 dni), Ty zaznaczasz, które trafią do planu dnia.
4. **Uwagi bez szukania.** Zaległe zadania, bot bez raportu, stara kopia zapasowa i oferty stojące za długo trafiają do jednej listy alertów na górze „Dziś” (a oferty także na górę Zarabiaj).
5. **Dwa urządzenia bez utraty zmian.** Konflikt zapisu nie wymaga powtarzania pracy: strona scala trzy wersje (ostatnio zsynchronizowaną, swoją i serwerową) po id. Serwer sam zmienia dane przy krokach ofert, więc scalanie jest potrzebne także przy jednym urządzeniu z nieodświeżoną kartą.
6. **Menu.** Dziś | Zarabiaj | Bot | Rynek (reszta stron analitycznych). Stare adresy `produkty.html`, `mentor.html`, `zarobki.html` przekierowują do Zarabiaj.

Czego nie sprawdzałem: wszystko działa w testach na atrapach (kalendarz iCal z przykładu, model AI jako funkcja, rclone i giełdy jako atrapy) i w przeglądarce (Chromium) na prawdziwym API z tymi atrapami. Nie było próby z Twoim kalendarzem, prawdziwym modelem ani serwerem; pierwsze uruchomienie jest testem integracji.
