# 🤖 Aiden

Agente de IA em português que roda no terminal e controla o PC de verdade — mexe em arquivo, roda comando, abre app, clica, tira screenshot e vê a tela.

## O que tem no projeto
- **aiden_core.py**: O cérebro do Aiden (memória, conversas, ferramentas).
- **aiden_pc.py**: O Aiden principal em modo texto (terminal).
- **aiden_gui.py**: Interface com janelas normais.
- **aiden_tui.py**: Interface de terminal com painéis coloridos.
- **aiden_hud.py, aiden_arc.py, aiden_stark.py**: Painéis futuristas estilo Jarvis.
- **aiden_auth.py**: Faz o login e conecta no servidor.
- **aiden_wake.py**: Fica ouvindo o microfone. Quando você fala "Aiden", ele abre o assistente.

## Como baixar e instalar
1. Vá até a página deste projeto no GitHub, clique no botão verde "Code" e escolha "Download ZIP". Extraia a pasta e abra o terminal dentro dela.
2. Crie o ambiente virtual e instale as dependências:
   No Windows:
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
3. Configure o servidor:
   [Environment]::SetEnvironmentVariable("AIDEN_SERVER", "https://seu-link-aqui", "User")
4. Rodar o Aiden:
   python aiden_pc.py
