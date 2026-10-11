# MultiDesk

Um mouse e um teclado para vários computadores, no Windows e no Linux.
É a mesma ideia do [Deskflow](https://github.com/deskflow/deskflow): você leva o cursor
até a borda da tela e ele "passa" para o outro PC, junto com o teclado e a área de
transferência (texto).

Foi escrito do zero em Python: não usa código do Deskflow.

## Como funciona

- **Servidor**: o PC que tem o mouse e o teclado físicos.
- **Cliente**: os PCs controlados. Eles se conectam usando o **link** que o servidor mostra:

  ```
  multidesk://192.168.0.11:24850/K7QM-2XPA-9RTD
  ```

  O link tem o IP, a porta e o **código de pareamento**. O código fica salvo em
  `~/.multidesk.json`, então o link continua o mesmo depois de reiniciar.

- Toda a comunicação é criptografada (X25519 + AES-GCM). Sem o código, ninguém conecta,
  e quem só escuta a rede não consegue ver o que você digita.
- Dá para conectar até 4 clientes, um em cada lado da tela do servidor (direita, esquerda, acima, abaixo).
- **Scroll Lock** prende o cursor na tela em que ele está (bom para jogos). Aperte de novo para soltar.
- Se um cliente cai enquanto você está controlando ele, o controle volta na hora para o servidor.
- A janela mostra o **ping** de cada conexão, atualizado a cada segundo
  (verde até 30 ms, amarelo até 100 ms, vermelho acima disso).

## Instalação

Os instaladores ficam na pasta `dist/` e já trazem tudo de que o programa precisa.

### Windows

1. Dê dois cliques em **`MultiDesk-Setup-0.2.0.exe`** e avance o assistente.
   Não pede senha de administrador.
2. O MultiDesk aparece no Menu Iniciar (e na área de trabalho, se você marcar a opção).
3. Para remover: *Configurações → Aplicativos → MultiDesk → Desinstalar*.

Se o Windows mostrar "O Windows protegeu o computador" (SmartScreen), clique em
**Mais informações → Executar assim mesmo**. Isso acontece com qualquer programa que não
tem assinatura digital paga.

Na primeira vez que o servidor iniciar, o Firewall do Windows vai perguntar se pode liberar
o programa: permita em **redes privadas**.

### Linux (Ubuntu, Debian, Mint, Pop!_OS, Zorin...)

1. Dê dois cliques em **`multidesk_0.2.0_all.deb`**. Ele abre na loja de aplicativos: clique em **Instalar**.
   Pelo terminal: `sudo apt install ./multidesk_0.2.0_all.deb` (use `apt`, não `dpkg -i`:
   só o `apt` baixa as dependências).
2. **Reinicie o computador** (ou saia e entre na sessão) na primeira instalação, para liberar
   o teclado/mouse virtuais.
3. O MultiDesk aparece no menu de aplicativos.
4. Para remover: pela loja de aplicativos, ou `sudo apt remove multidesk`.

**Wayland ou X11?** Para ser **controlado** (cliente), o Linux funciona nos dois: o MultiDesk
cria um teclado e um mouse virtuais no kernel (`/dev/uinput`), como se fossem USB.
Para **controlar** os outros (servidor), o Linux precisa de sessão **X11**, porque o Wayland
não deixa programas capturarem o mouse e o teclado. Na tela de login, clique na engrenagem e
escolha **"Ubuntu on Xorg"** (ou "GNOME on Xorg" / "Plasma (X11)").

Se o servidor for Linux e usar firewall: `sudo ufw allow 24850/tcp`.

### Segundo plano e iniciar com o sistema

Com o MultiDesk ativo, **fechar a janela não encerra o programa**: ele continua rodando com um
ícone na bandeja do sistema (perto do relógio no Windows; na barra superior no Ubuntu).
Clique no ícone para abrir a janela; o menu dele também tem **Iniciar/Parar** e **Sair**.

Ligue **"Iniciar com o sistema"** na janela para que, ao ligar o computador, o MultiDesk abra
direto na bandeja e já comece no modo que você usou por último (servidor ou cliente).

> Linux: o ícone usa AppIndicator. O Ubuntu já vem com isso; no GNOME "puro" (Fedora,
> Debian) instale a extensão *AppIndicator and KStatusNotifierItem Support*. Sem ela o
> MultiDesk roda em segundo plano do mesmo jeito: abra pelo menu de aplicativos para ver a janela.

O log fica em `%LOCALAPPDATA%\MultiDesk\multidesk.log` (Windows) ou
`~/.local/state/multidesk/multidesk.log` (Linux).

## Uso pela janela

1. No PC com o mouse/teclado: escolha **Compartilhar este PC**, clique em **Copiar** para copiar
   o link e em **Iniciar**.
2. No outro PC: escolha **Ser controlado**, cole o link, clique no desenho para dizer de que lado
   ele fica em relação ao servidor e clique em **Iniciar**.
3. Leve o mouse até aquela borda da tela. O desenho do servidor mostra cada PC conectado com o
   ping, e destaca em verde o que você está controlando.

## Uso pela linha de comando

Depois de instalado no Linux o comando é `multidesk`. No código-fonte use `python -m multidesk`.

```bash
# servidor (mostra o link)
python -m multidesk server
python -m multidesk server --left notebook --right pc-sala   # fixa os lados pelo nome do cliente

# cliente
python -m multidesk client multidesk://192.168.0.11:24850/K7QM-2XPA-9RTD --side right
```

Opções úteis: `--port`, `--code`, `--name`, `--no-clipboard`, `-v` (log detalhado).

## Limitações conhecidas

- Área de transferência: só texto, sem arquivos nem imagens.
- Windows: o cliente não consegue controlar janelas abertas **como administrador**
  (por exemplo o Gerenciador de Tarefas), a menos que o MultiDesk também rode como administrador.
  Ctrl+Alt+Del não pode ser repassado.
- Linux como servidor: só X11 (veja acima). Como cliente funciona no Wayland e no X11.
- Os clientes ficam encostados no servidor: não dá para ir de um cliente direto para outro.
- Os dois PCs precisam estar na mesma rede, ou alcançáveis pelo IP do link (VPN como Tailscale/ZeroTier serve).

## Gerar os instaladores

```powershell
# Windows -> dist\installer\MultiDesk-Setup-<versão>.exe  (precisa do Inno Setup 6)
powershell -ExecutionPolicy Bypass -File build_windows.ps1

# Linux -> dist/multidesk_<versão>_all.deb  (roda em qualquer sistema, inclusive Windows)
python tools/build_deb.py
```

A versão fica em `multidesk/__init__.py`. Para rodar direto do código-fonte, sem instalar,
use `iniciar.bat` (Windows) ou `iniciar.sh` (Linux).

## Estrutura

| Arquivo | O que faz |
|---|---|
| `multidesk/server.py` | Detecta a borda, troca de tela e envia os eventos |
| `multidesk/client.py` | Recebe os eventos e simula mouse/teclado; reconecta sozinho |
| `multidesk/crypto.py` | Handshake com o código de pareamento e canal criptografado |
| `multidesk/platform/windows.py` | Hooks de baixo nível e SendInput |
| `multidesk/platform/linux.py` | Captura no X11 (RECORD/XGrab); simulação via uinput ou XTest |
| `multidesk/platform/uinput.py` | Teclado e mouse virtuais do kernel (Wayland e X11) |
| `multidesk/keymap.py` | Tradução de teclas físicas: scan code do Windows ↔ evdev do Linux |
| `multidesk/clipboard.py` | Sincroniza a área de transferência |
| `multidesk/gui.py` | A janela (customtkinter) |
| `multidesk/tray.py` | Ícone na bandeja do sistema (pystray) |
