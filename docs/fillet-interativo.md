# Cascade CAD 0.2 — Fillet por arraste

A referencia de interacao e o [Fillet Shell do Plasticity](https://doc.plasticity.xyz/solid/fillet-shell): arestas selecionadas, alca amarela, atalho B e distancia positiva para fillet ou negativa para chanfro. O calculo deste plugin continua sendo Open CASCADE.

## Como usar

1. Em Object Mode, selecione um objeto Cascade CAD.
2. Clique **Selecionar arestas**. Clique nas arestas desejadas, ou pressione **A** para todas.
3. Pressione **B** para entrar diretamente no fillet. Voce tambem pode confirmar a selecao com Enter e depois usar **B** ou o botao **Fillet / Chanfro: arrastar**.
4. No viewport, pressione o botao esquerdo sobre a **alca amarela** e arraste horizontalmente. Soltar o mouse encerra o arraste e permite continuar ajustando.
5. Confirme com **Enter** ou **botao direito**. **Esc** cancela e restaura o original.

| Controle | Acao |
|---|---|
| Arrastar a alca amarela | Alterar a distancia com previa CAD |
| Shift durante o arraste | Ajuste dez vezes mais preciso |
| Ctrl durante o arraste | Passos de 0,5 mm |
| Digitar um numero | Distancia exata em mm; aceita virgula ou ponto |
| C | Chanfro, distancia negativa |
| D | Fillet, distancia positiva |
| Ctrl+clique numa aresta original | Adicionar/remover essa aresta da operacao |
| Enter ou botao direito | Confirmar a geometria final |
| Esc | Cancelar sem modificar a malha nem o BREP |
| Ctrl+Z depois de confirmar | Desfazer a operacao completa |

A distancia pode atravessar o zero durante o arraste: positiva arredonda; negativa cria um chanfro. Os campos do painel e os botoes **Aplicar raio** / **Chanfro fixo** continuam disponiveis para aplicar um valor diretamente.

## Previa e confirmacao

Um processo local mantem o Open CASCADE carregado. Durante o arraste, ele calcula novas formas a partir do **solido original**, sem acumular arredondamentos. O Blender exibe essas superficies por GPU; a malha original e o BREP so mudam na confirmacao. A confirmacao gera novamente o CAD com a tolerancia final configurada no painel.

O valor grande no viewport e a distancia solicitada. Enquanto o calculo acompanha o mouse, a mensagem informa a distancia da previa atualmente exibida. A atualizacao continua e limitada pela complexidade da geometria e pelo tempo de calculo do Open CASCADE. A primeira abertura tambem precisa carregar o motor; selecionar arestas ja inicia esse carregamento.

Se um raio for inviavel, a ferramenta mostra uma mensagem e conserva a ultima previa valida. Ela **nao confirma essa previa antiga**: reduza o raio ou altere as arestas. Esc sempre restaura o original. Um calculo que ultrapasse 20 segundos e cancelado. O processo de previa e encerrado ao desabilitar o plugin, abrir outro arquivo ou sair do Blender.

## Escopo

Esta versao implementa fillet circular de raio constante e chanfro de distancias iguais nas duas faces. Ainda nao implementa os perfis G2, conicos, tensao, pontos de raio variavel, limites de trecho ou controle de angulo do Plasticity. O Open CASCADE pode propagar certos fillets por conexoes tangentes; a selecao e o resultado dependem da topologia.

## Instalacao e arquivos

Instale **CascadeCAD-0.2.0.zip** pelo menu **Install from Disk** das extensoes do Blender. O motor da versao 0.1 e reutilizado.

Veja a captura em docs/images/fillet-preview.png. O arquivo Teste_Fillet_020.blend abre um bloco pronto para pressionar B. O codigo editavel esta em cascade_cad/; os testes e relatorios estao em tests/ e validation/.
