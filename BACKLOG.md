# Backlog

## US-017 — Osoba składająca PC rozpoznaje nazwane warianty w porównaniu  [w toku]

Jako osoba składająca PC chcę nadać nazwy zapisanym zestawom i widzieć je podczas porównania, żeby łatwo rozpoznać warianty oraz rekomendowany wybór.

- Dlaczego teraz: PROJECT.md wymaga porównywania zapisanych konfiguracji jako wariantów i czytelnego podsumowania rekomendacji, a dostarczone US-014–US-016 wskazują wybór za pomocą identyfikatora zamiast nazwy zrozumiałej dla osoby porównującej.
- Sprawdzenie: uruchom demo, zapisz dwa zestawy pod różnymi nazwami, porównaj je i potwierdź, że ekran pokazuje obie nazwy oraz używa właściwej nazwy przy rekomendowanym wariancie także po ponownym otwarciu aplikacji.
- Poza zakresem: zmiana nazwy po zapisaniu, lista zapisów, opisy i tagi, konta użytkowników oraz porównanie więcej niż dwóch zestawów.

## US-016 — Osoba składająca PC wybiera tańszy z równie bezpiecznych wariantów  [do weryfikacji]

Jako osoba składająca PC chcę przy porównaniu równie bezpiecznych zestawów zobaczyć wskazanie tańszego wariantu, żeby ograniczyć koszt bez rezygnacji ze zgodności ani budżetu.

- Dlaczego teraz: PROJECT.md wymaga, aby koszty i rekomendacje wspierały wybór, task-029 potwierdził wskazanie tańszego zestawu, task-040 rekomendację według zgodności, a task-043 rekomendację według budżetu.
- Sprawdzenie: uruchom demo, porównaj dwa zestawy bez blokujących konfliktów i z takim samym wynikiem budżetowym, ale o różnych kosztach, i potwierdź, że poza informacją o niższym koszcie ekran pokazuje odrębną rekomendację końcową tańszego wariantu, a przy równych kosztach nie pokazuje rekomendacji końcowej.
- Poza zakresem: przedkładanie ceny nad zgodność lub budżet, ocena wydajności i zastosowania, proponowanie zamienników oraz porównanie więcej niż dwóch zestawów.

## US-015 — Osoba składająca PC wybiera wariant mieszczący się w budżecie  [do weryfikacji]

Jako osoba składająca PC chcę przy porównaniu dwóch zestawów bez blokujących konfliktów zobaczyć wskazanie wariantu mieszczącego się w swoim budżecie, żeby nie wybrać konfiguracji przekraczającej mój limit wydatków.

- Dlaczego teraz: PROJECT.md uznaje budżet i rekomendacje za część wiarygodnego podsumowania, task-038 potwierdził odrębne oceny budżetów wariantów, a task-040 potwierdził rekomendację ograniczoną do blokujących konfliktów.
- Sprawdzenie: uruchom demo, porównaj dwa zestawy bez blokujących konfliktów, z których tylko jeden mieści się w swoim budżecie, i potwierdź, że ekran wskazuje ten wariant, a przy dwóch zestawach mieszczących się w budżecie nie ogłasza zwycięzcy.
- Poza zakresem: rekomendowanie na podstawie ceny, wysokości pozostałej kwoty, wydajności lub zastosowania, proponowanie zamienników i porównanie więcej niż dwóch zestawów.

## US-014 — Osoba składająca PC wybiera wariant bez blokującego konfliktu  [do weryfikacji]

Jako osoba składająca PC chcę przy porównaniu zobaczyć wskazanie wariantu bez blokującego konfliktu, żeby nie wybrać zestawu, którego nie da się poprawnie złożyć.

- Dlaczego teraz: PROJECT.md uznaje rekomendacje za część wiarygodnego podsumowania, a raport task-036 potwierdził odrębną ocenę zgodności wariantów bez wskazania bezpieczniejszego wyboru.
- Sprawdzenie: uruchom demo, porównaj zgodny zapis z zapisem mającym blokujący konflikt i potwierdź, że ekran wskazuje zgodny wariant, a przy dwóch zgodnych zapisach nie ogłasza zwycięzcy.
- Poza zakresem: rekomendowanie na podstawie ceny, budżetu, wydajności lub zastosowania, naprawianie konfliktów i porównanie więcej niż dwóch zestawów.

## US-001 — Osoba składająca PC sprawdza zgodność konkretnych części  [do weryfikacji]

Jako osoba składająca PC chcę wybrać konkretny procesor i płytę główną, żeby przed zakupem zobaczyć, czy mogą działać razem.

- Dlaczego teraz: PROJECT.md wskazuje katalog CPU i płyt oraz wybieranie rzeczywistych produktów jako pierwszy prawdopodobny etap po działającym szkielecie.
- Sprawdzenie: uruchom demo, wybierz zgodną, a następnie niezgodną parę nazwanych produktów i potwierdź, że widoczny wynik zmienia się odpowiednio.
- Poza zakresem: pozostałe kategorie części, ceny, import sklepowy, zapis konfiguracji i reguły inne niż zgodność procesora z płytą.

## US-002 — Osoba składająca PC sprawdza zgodność pamięci z płytą główną  [do weryfikacji]

Jako osoba składająca PC chcę dobrać pamięć RAM do wybranej płyty głównej, żeby przed zakupem zobaczyć, czy pamięć będzie z nią zgodna.

- Dlaczego teraz: PROJECT.md wskazuje reguły RAM jako najbliższy etap po katalogu CPU i płyt oraz wymaga, aby ocena zestawu uwzględniała pamięć.
- Sprawdzenie: uruchom demo, wybierz płytę główną oraz kolejno zgodną i niezgodną pamięć, a następnie potwierdź, że widoczna ocena zestawu rozróżnia oba przypadki.
- Poza zakresem: pojemność i wydajność pamięci, procesorowe ograniczenia pamięci, zasilanie, montaż fizyczny, ceny i pozostałe kategorie części.

## US-003 — Osoba składająca PC sprawdza, czy zasilacz wystarczy zestawowi  [do weryfikacji]

Jako osoba składająca PC chcę dobrać zasilacz do wybranych części, żeby przed zakupem zobaczyć, czy zapewni im wystarczającą moc.

- Dlaczego teraz: PROJECT.md wymaga uwzględnienia zasilania w ocenie zestawu, a żadna obecna historyjka nie pokrywa tego wymagania.
- Sprawdzenie: uruchom demo, wybierz zestaw części oraz kolejno zasilacz o wystarczającej i niewystarczającej mocy, a następnie potwierdź, że widoczna ocena rozróżnia oba przypadki.
- Poza zakresem: zgodność złączy, sprawność i jakość zasilacza, zapas mocy, ceny, montaż fizyczny oraz pozostałe kategorie części.

## US-004 — Osoba składająca PC sprawdza dopasowanie płyty głównej do obudowy  [do weryfikacji]

Jako osoba składająca PC chcę dobrać obudowę do wybranej płyty głównej, żeby przed zakupem zobaczyć, czy płyta zmieści się w obudowie.

- Dlaczego teraz: PROJECT.md wskazuje fizyczny montaż obok reguł RAM i PSU jako prawdopodobny etap, a po objęciu RAM i zasilania jest to jedyny z tych trzech obszarów niepokryty historyjką.
- Sprawdzenie: uruchom demo, wybierz płytę główną oraz kolejno pasującą i niepasującą obudowę, a następnie potwierdź, że widoczna ocena rozróżnia oba przypadki.
- Poza zakresem: dopasowanie karty graficznej, chłodzenia i zasilacza, przepływ powietrza, złącza panelu obudowy, ceny oraz pozostałe zależności montażowe.

## US-005 — Osoba składająca PC widzi łączny koszt wybranych części  [do weryfikacji]

Jako osoba składająca PC chcę widzieć łączny koszt wybranych części, żeby od razu ocenić, ile kosztuje mój aktualny zestaw.

- Dlaczego teraz: PROJECT.md wskazuje koszty jako kolejny prawdopodobny etap po regułach RAM, PSU i fizycznego montażu oraz wymaga, aby koszt był widoczny bez szukania.
- Sprawdzenie: uruchom demo, wybierz kolejno kilka nazwanych części i potwierdź po każdym wyborze, że widoczny łączny koszt odpowiada aktualnemu zestawowi.
- Poza zakresem: budżet, historia i porównanie cen, oferty wielu sklepów, dostawa, rabaty oraz optymalizacja koszyka.

## US-006 — Osoba składająca PC sprawdza, czy zestaw mieści się w budżecie  [do weryfikacji]

Jako osoba składająca PC chcę podać swój budżet i zobaczyć, czy koszt wybranych części go przekracza, żeby kontrolować wydatki podczas kompletowania zestawu.

- Dlaczego teraz: PROJECT.md wskazuje budżet jako kolejny etap wraz z kosztami, a obecna historyjka kosztowa pokazuje kwotę bez odniesienia jej do limitu użytkownika.
- Sprawdzenie: uruchom demo, ustaw budżet kolejno powyżej i poniżej kosztu aktualnego zestawu i potwierdź, że widoczna ocena rozróżnia oba przypadki oraz pokazuje pozostałą kwotę lub przekroczenie.
- Poza zakresem: rekomendowanie zamienników, blokowanie wyboru części, wiele budżetów, zapis konfiguracji, import i historia cen oraz oferty sklepów.

## US-007 — Osoba składająca PC zachowuje swój zestaw  [do weryfikacji]

Jako osoba składająca PC chcę zapisać aktualny zestaw i później otworzyć go ponownie, żeby nie stracić dokonanych wyborów.

- Dlaczego teraz: PROJECT.md wskazuje zapis konfiguracji jako prawdopodobny etap po kosztach i budżecie, które pokrywają już istniejące historyjki.
- Sprawdzenie: uruchom demo, wybierz części i budżet, zapisz zestaw, uruchom demo ponownie i potwierdź, że otwarty zestaw zawiera te same wybory oraz budżet.
- Poza zakresem: konta użytkowników, lista wielu zapisów, edycja nazwy, udostępnianie, porównywanie wariantów i synchronizacja między urządzeniami.

## US-008 — Osoba składająca PC udostępnia zapisany zestaw  [do weryfikacji]

Jako osoba składająca PC chcę przekazać zapisany zestaw drugiej osobie, żeby mogła zobaczyć te same części, budżet i oceny.

- Dlaczego teraz: PROJECT.md stawia udostępnianie zestawów po ich zapisywaniu w celu docelowym, a zapis i ponowne otwarcie zestawu zostały już dostarczone do weryfikacji.
- Sprawdzenie: uruchom demo, zapisz zestaw, przekaż uzyskany odnośnik drugiej osobie i potwierdź w osobnej sesji, że otwiera on te same części, budżet i widoczne oceny.
- Poza zakresem: konta użytkowników, uprawnienia dostępu, wspólna edycja, wygasanie odnośników, media społecznościowe i porównywanie wariantów.

## US-009 — Osoba składająca PC porównuje koszt dwóch zapisanych zestawów  [do weryfikacji]

Jako osoba składająca PC chcę zestawić koszty dwóch zapisanych konfiguracji, żeby łatwo wybrać tańszy wariant.

- Dlaczego teraz: PROJECT.md wymaga porównywania zapisanych konfiguracji jako wariantów, a zapis i udostępnianie zestawów zostały już dostarczone do weryfikacji.
- Sprawdzenie: uruchom demo, zapisz dwa zestawy o różnych kosztach, porównaj je i potwierdź, że widzisz oba koszty oraz wskazanie tańszego zestawu.
- Poza zakresem: porównanie wydajności i zgodności, różnice między pojedynczymi częściami, rekomendowanie wariantu, nazwy zestawów i porównanie więcej niż dwóch zestawów.

## US-010 — Osoba składająca PC widzi różnice części między dwoma zestawami  [do weryfikacji]

Jako osoba składająca PC chcę zobaczyć, które części różnią dwa zapisane zestawy, żeby zrozumieć, z czego wynika wybór między wariantami.

- Dlaczego teraz: PROJECT.md wymaga porównywania zapisanych konfiguracji jako wariantów, a porównanie kosztów dwóch zestawów zostało już dostarczone do weryfikacji bez pokazania różnic ich części.
- Sprawdzenie: uruchom demo, zapisz dwa zestawy różniące się częścią, porównaj je i potwierdź, że ekran wskazuje różną część oraz nie oznacza wspólnych wyborów jako różnic.
- Poza zakresem: porównanie wydajności i zgodności, rekomendowanie wariantu, różnice cen pojedynczych części, nazwy zestawów i porównanie więcej niż dwóch zestawów.

## US-011 — Osoba składająca PC widzi różnice cen części między dwoma zestawami  [do weryfikacji]

Jako osoba składająca PC chcę przy każdej różniącej się części zobaczyć ceny obu wariantów, żeby zrozumieć, które wybory odpowiadają za różnicę kosztu zestawów.

- Dlaczego teraz: raport task-032 potwierdził, że porównanie pokazuje już nazwy różnych części, ale nie ich ceny, choć PROJECT.md wymaga, aby ceny wspierały wybór.
- Sprawdzenie: uruchom demo, zapisz dwa zestawy różniące się częścią, porównaj je i potwierdź, że przy tej części widać ceny obu wyborów oraz ich różnicę w PLN.
- Poza zakresem: historia i źródła cen, dostawa, rekomendowanie wariantu, porównanie wydajności i zgodności oraz porównanie więcej niż dwóch zestawów.

## US-012 — Osoba składająca PC porównuje zgodność dwóch zapisanych zestawów  [do weryfikacji]

Jako osoba składająca PC chcę przy porównaniu dwóch zapisanych zestawów zobaczyć zgodność każdego z nich, żeby nie wybrać tańszego wariantu z blokującym problemem.

- Dlaczego teraz: PROJECT.md wymaga porównywania konfiguracji jako wariantów oraz wiarygodnego podsumowania zgodności, a raport task-034 potwierdził domknięcie wyjaśnienia różnic kosztowych bez oceny zgodności wariantów.
- Sprawdzenie: uruchom demo, zapisz jeden zgodny zestaw i jeden zestaw z blokującym konfliktem, porównaj je i potwierdź, że ekran odrębnie pokazuje zgodność obu wariantów oraz wskazuje problematyczny zestaw.
- Poza zakresem: rekomendowanie lepszego wariantu, porównanie wydajności, ostrzeżenia nieblokujące, naprawianie konfliktów i porównanie więcej niż dwóch zestawów.

## US-013 — Osoba składająca PC porównuje zestawy względem ich budżetów  [do weryfikacji]

Jako osoba składająca PC chcę przy porównaniu dwóch zapisanych zestawów zobaczyć, czy każdy z nich mieści się we własnym budżecie, żeby koszt tańszego wariantu nie przesłonił mojego limitu wydatków.

- Dlaczego teraz: PROJECT.md wymaga uwzględnienia budżetu w ocenie zestawu, a raport task-036 potwierdził widoczną zgodność obu wariantów bez pokazania ich oceny budżetowej.
- Sprawdzenie: uruchom demo, zapisz dwa zestawy z budżetami ustawionymi odpowiednio powyżej i poniżej ich kosztów, porównaj je i potwierdź, że ekran osobno pokazuje pozostałą kwotę oraz przekroczenie budżetu.
- Poza zakresem: rekomendowanie wariantu lub zamienników, wspólny budżet dla obu zestawów, edycja zapisów, historia cen i porównanie więcej niż dwóch zestawów.
