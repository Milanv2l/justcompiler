//This is made using ai

Optie 1: Installeren op Windows (10 of 11)

Als je op een Windows computer werkt, gebruik je de PowerShell-methode:

    Klik op het Startmenu en zoek naar PowerShell.

    Klik met je rechtermuisknop op "Windows PowerShell" en kies "Als administrator uitvoeren" (Dit is belangrijk omdat het script mogelijk Docker en Python via Winget moet installeren).

    Kopieer en plak de volgende regel in het blauwe PowerShell venster en druk op Enter:
    PowerShell

    irm https://raw.githubusercontent.com/Milanv2l/Autobuilder/main/Windows/setup.ps1 | iex

    Het script gaat nu lopen. Je ziet de installatie-stappen en hij controleert of je Docker hebt.

    Als de installatie klaar is en een groen vinkje geeft, sluit je dit PowerShell venster helemaal af.

Optie 2: Installeren op Linux (Fedora, Ubuntu, etc.)

Als je op Linux werkt, gebruik je de Bash-methode:

    Open je Terminal.

    Kopieer en plak de volgende regel in je terminal en druk op Enter:
    Bash

    curl -fsSL https://raw.githubusercontent.com/Milanv2l/Autobuilder/main/Linux/setup.sh | bash

    Het script zal je waarschijnlijk één keer om je wachtwoord (sudo) vragen om eventueel Docker te installeren of de Docker-service in te schakelen.

    Als de installatie klaar is, typ je source ~/.bashrc (of sluit je de terminal af en open je een nieuwe).
