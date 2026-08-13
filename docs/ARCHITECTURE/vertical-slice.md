# Pionowy szkielet

`src/server.py` serwuje ekran i endpoint `/api/analyze`. Endpoint wywoluje `analyze_build` z `src/analyze_build.py`; przegladarka wywoluje endpoint po zmianie wyboru. Test uruchamia `create_app`, wiec sprawdza ten sam HTTP i modul analizy co aplikacja uzytkowniczki.

## Weryfikacja

- start: `python3 -m src.server`
- test: `python3 -m unittest`
- build: `python3 -m compileall -q src`
- smoke: `python3 -m unittest test.test_app`
- ci: `python3 -m unittest`
- hardware: `python3 -m unittest test.test_app`
