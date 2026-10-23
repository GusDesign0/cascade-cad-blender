# Reproduzir as verificacoes

## Versao 0.2: fillet interativo

Depois dos testes de regressao abaixo, execute `tests/test_preview_engine.py` com o Python do motor CAD. Ele gera a fixture em `work/fillet_fixture.json` e verifica o motor persistente, a fila de pedidos e os resultados geometricos. Em seguida, execute `tests/test_live_fillet_ui.py` no Blender com `--factory-startup --enable-event-simulate --window-geometry 50 50 1500 1000 --python`. Esse teste verifica arraste continuo, Shift/Ctrl, selecao de arestas, entrada numerica, erros, confirmacao, cancelamento e undo/redo. `tests/test_preview_lifecycle.py` testa cancelamento de calculo pendente e limpeza ao abrir outro arquivo. Esses testes abrem e fecham janelas proprias; nao interaja com elas durante a execucao.

Os tempos medidos nos relatorios sao do calculo de um bloco simples neste computador; nao garantem a mesma latencia em modelos complexos. O video da versao 0.1 nao e regravado por esses testes.

Execute a partir da pasta principal do pacote. Os scripts gravam os relatorios e regeneram o exemplo/previas; nao use uma pasta com arquivos que voce tenha modificado.

1. `python test_worker.py`: use o Python do motor CAD, apontando para `tests/test_worker.py`. Testa volumes, booleanas, transformacoes, fillet, chanfro, shell, push/pull, perfis e STEP.
2. `blender --background --factory-startup --python tests/test_blender.py`: testa a integracao, preservacao em erro, deteccao de edicao manual, BREP persistido e renderiza a previa.
3. `blender --factory-startup --enable-event-simulate --window-geometry 50 50 1500 1000 --python tests/test_ui.py`: abre uma janela real e usa a API de simulacao de eventos do Blender para testar selecao, desenho, extrusao assincrona e undo/redo. Fecha a janela ao concluir. Nao interaja com essa janela durante o teste.

O pacote entregue foi verificado no Blender 5.2.2 LTS, Windows x64, Python 3.12 e CadQuery 2.6.1/OCP 7.8.1.1. Outras versoes nao foram verificadas.


## Versao 0.3: remesh protegido

`blender -b -t 4 --factory-startup --python tests/test_remesh.py` testa conversao fiel, snapshot imutavel, parent/matriz, UV, materiais, rejeicao de origem alterada e malha aberta. A fixture CAD esta incluida; nao precisa executar os outros testes antes.

`blender -t 4 --factory-startup --enable-event-simulate --python tests/test_remesh_gui.py` verifica cancelamento, overlay GPU, aplicacao por menu real, undo/redo e cancelamento ao carregar outro arquivo. Abre e fecha uma janela propria.

A validacao da KAIRO e um benchmark artistico separado, com 49 objetos; o relatorio e a cena ficam nos assets da release. Nao e uma garantia de desempenho ou fidelidade para outros modelos.
