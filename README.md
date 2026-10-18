# 🤖 Aiden

Agente de IA em português que roda no terminal e controla o PC de verdade — mexe em arquivo, roda comando, abre app, clica, tira screenshot e vê a tela.

## O que tem no projeto

O Aiden é dividido em um núcleo e várias interfaces visuais:
- aiden_core.py: O cérebro (memória, conversas, ferramentas).
- aiden_pc.py: O Aiden principal em modo texto (terminal).
- aiden_gui.py: Interface em janelas normais.
- aiden_tui.py: Interface de terminal com painéis coloridos.
- aiden_hud.py, aiden_arc.py, aiden_stark.py: Painéis futuristas estilo Jarvis.
- aiden_auth.py: Faz o login e conecta no servidor.

O que ele faz:
- Lê, cria e edita arquivos no seu PC.
- Roda comandos no terminal.
- Abre programas e navegadores.
- Controla o mouse e o teclado.
- Tira print e analisa a tela (visão).
- Guarda memória do que você fala.

## Como baixar e instalar

1. Baixar o projeto
Vá até a página deste projeto no GitHub, clique no botão verde "Code" e escolha "Download ZIP". Extraia a pasta no seu computador e abra o terminal dentro dela.

2. Preparar o ambiente
No Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

No Linux ou macOS:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

3. Configurar o servidor
O Aiden não guarda a chave da API, ele se conecta a um servidor. Você precisa colar o link do seu servidor no terminal para configurar.

No Windows:
[Environment]::SetEnvironmentVariable("AIDEN_SERVER", "https://seu-link-aqui", "User")
(Feche e abra o terminal de novo depois disso)

No Linux ou macOS:
echo 'export AIDEN_SERVER="https://seu-link-aqui"' >> ~/.bashrc
source ~/.bashrc

4. Rodar o Aiden
python aiden_pc.py

Na primeira vez, ele vai pedir seu usuário e senha. Depois disso, ele lembra de você e pergunta se quer continuar logado.

## Comandos Rápidos

Digite no terminal do Aiden:
- /gui ou /tui : Abre outras interfaces visuais.
- /pasta CAMINHO : Define a pasta do projeto para ele mexer.
- /plan : Modo só leitura (não altera arquivos).
- /dev : Modo programador (foco em código).
- /login e /logout : Entra ou sai da sua conta.
- /limpar : Limpa o histórico da tela.
- /ajuda : Mostra todos os comandos.

## Segurança
- O Aiden sempre pede permissão antes de editar arquivos, rodar comandos perigosos ou controlar o mouse.
- Senhas e chaves de API nunca são salvas no seu computador.
- Ele não lê pastas ocultas do sistema ou chaves SSH.

## Licença
MIT — você pode usar, modificar e compartilhar à vontade.
