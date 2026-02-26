# TODO

- [x] Aggiungere un coordinator (semaphore) per far partire la gara in automatico quando gli agenti sono pronti
- [x] Aggiungere eventi interni (eventI) per simulare la proattività, come per esempio un evento che simula un guasto alla macchina o livello del carburante basso, che costringe l'agente a fermarsi ai box
- [x] Aggiungere più agenti per rendere la gara più interessante (ora basta aggiungere una entry in agents.json e runnare generate_agents.py)
- [x] Fare in modo che la ui si crei in modo dinamico in base al numero di agenti presenti e non tenerli hardcodati nel backend (agents.json + generate_agents.py)
- [x] Aggiungere un tabellone finale che faccia vedere il posizionamento dei vari piloti
- [x] Green flag del racedirector parte spesso (togliere o modificare)
- [x] Fare il porting su Docker
- [x] Aggiungere handler di kill per sicstus quando mando CTRL+C al terminale della UI

### Times needed
- [x] Fare UI con grafica con circuito e macchine
- [x] Fare sequence diagrams
- [x] Fare documentazione
- [x] Ripulire le varie scritte degli eventi
- [x] Velocizzare lo startmas


### Bug
- [x] Fixare bug "External  precondition ... noDeltatime" (partially solved)
- [x] Non sempre lo startmas fa partire correttamente il programma
- [x] La UI ci mette eccessivamente ad aprirsi, probabiomente perchè prova a riscaricare le librerie o a ricreare ogni volta il venv
- [x] Controllare se il restart nella circuit tab si blocca
- [x] Correggere SPIO_E_NET_ADDRINUSE
- [ ] Race condition per il restart del mas
- [ ] **[RESTART/ADDRINUSE]** Serializzare il kill in `dashboard.py`: attendere conferma pgrep di `active_server_wi.pl` morto *prima* di eseguire `tmux kill-session`, così tmux non manda SIGHUP a LINDA che farebbe shutdown graceful → FIN → TIME_WAIT su 3010
- [ ] **[RESTART/ADDRINUSE]** In `_kill_all()`, sostituire `tmux kill-session` con kill diretto al PID del pane (`kill -9 $(tmux list-panes -t f1_race:server -F "#{pane_pid}")`) per bypassare il meccanismo SIGHUP di tmux
- [ ] **[RESTART/ADDRINUSE]** Aggiungere in `startmas.sh` dopo il cleanup un log di `ss -tn state time-wait | grep ":3010"` via `_tick` per diagnosticare se e quanti socket TIME_WAIT restano e da dove vengono
- [ ] **[RESTART/ADDRINUSE]** Fissare `active_user_wi.pl` per leggere la porta da `server.txt` (codice già presente ma commentato) — abiliterebbe la porta dinamica (3010/3011/…) senza rompere l'user agent ed eliminerebbe il problema alla radice

