# TODO

- [x] Aggiungere un coordinator (semaphore) per far partire la gara in automatico quando gli agenti sono pronti
- [x] Aggiungere eventi interni (eventI) per simulare la proattività, come per esempio un evento che simula un guasto alla macchina o livello del carburante basso, che costringe l'agente a fermarsi ai box
- [x] Aggiungere più agenti per rendere la gara più interessante (ora basta aggiungere una entry in agents.json e runnare generate_agents.py)
- [x] Fare in modo che la ui si crei in modo dinamico in base al numero di agenti presenti e non tenerli hardcodati nel backend (agents.json + generate_agents.py)
- [x] Aggiungere un tabellone finale che faccia vedere il posizionamento dei vari piloti
- [x] Green flag del racedirector parte spesso (togliere o modificare)
- [x] Fare il porting su Docker

### Times needed
- [x] Fare UI con grafica con circuito e macchine
- [x] Fare sequence diagrams
- [x] Fare documentazione
- [x] Ripulire le varie scritte degli eventi
- [x] Velocizzare lo startmas

### Bug
- [x] Fixare bug "External  precondition ... noDeltatime" (partially solved)
- [ ] Non sempre lo startmas fa partire correttamente il programma
- [x] La UI ci mette eccessivamente ad aprirsi, probabiomente perchè prova a riscaricare le librerie o a ricreare ogni volta il venv
- [ ] controllare se il restart nella circuit tab si blocca
