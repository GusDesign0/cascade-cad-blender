# Remesh de quads protegido - 0.3.0

## Fluxo

1. Salve uma versão da cena e selecione uma ou mais malhas fechadas em Object Mode.
2. N > CAD > CASCADE / Remesh de quads. Instale o motor opcional em Windows x64 ou escolha Superfície fiel.
3. Escolha método, densidade, tolerância em mm e guias. Gere a prévia.
4. O wire ciano é sobreposto ao original. Em malhas grandes é amostrado (o painel avisa); a malha final é completa.
5. Confira os métodos e o desvio. Aplicar cria cópias livres de CAD e oculta os originais. Descartar/Esc preserva tudo.

## Métodos e limites

| Método | Resultado |
|---|---|
| AUTO / Adaptativo protegido | QuadWild; fallback fiel se uma tentativa recuperável falhar ou exceder tolerância |
| NATIVE / QuadWild estrito | Rejeita tentativa inválida; uma ilha fechada por objeto |
| EXACT / Superfície fiel | Mantém origem já em quads; divide cada triângulo da superfície em três quads |

Densidade é fator relativo, não orçamento exato de faces. Maior valor pede quads menores. O solver pode refinar o candidato em quatro antes de reprojetar e validar. Modo fiel pode aumentar o número de faces e não é retopologia de loops artísticos.

Fontes abertas/nonmanifold ou degeneradas são recusadas. AUTO usa fiel em múltiplas ilhas. Todas as peças são processadas separadamente; contatos entre peças não são certificados. Uma falha fatal nativa encerra o subprocesso e cancela o trabalho, sem mutar a origem.

O snapshot usa geometria avaliada, transformações no mundo e Unit Scale da cena. Há tolerância em mm, limites por objeto, tentativas e tempo máximo. O processo usa quatro threads e pode consumir memória relevante em malhas grandes; não há promessa de latência interativa de remesh.

## Verificação

Todo candidato é comparado com a referência normalizada no mundo: amostras bidirecionais de vértices e centróides da triangulação, mais extremos e pontos médios de todos os segmentos-guia. O relatório registra quantidade de amostras, máximos e média. O máximo amostrado **não** é uma distância de Hausdorff certificada.

Também confere faces de quatro vértices, número de ilhas, Euler, arestas não manifold e faces degeneradas. Isso não prova ausência de autointerseções nem qualidade de loops de deformação. Faça inspeção visual de silhueta, sombreamento, furos, texturas e contatos antes de adotar a cópia.

Materiais, UV, cad_face e face sets são transferidos. Novos objetos mantêm parent e transformação, mas não os modificadores/constraints já avaliados nem a animação da origem. Não há transferência garantida de vertex colors, pesos, rig ou atributos arbitrários. Bump e normal de shader continuam efeitos de material, não são convertidos em geometria.

Aplicar verifica se a geometria avaliada, matriz e nomes de materiais de origem ainda correspondem ao snapshot. Alterações invalidam a prévia. Abrir outra cena ou desabilitar a extensão encerra processos e remove overlays.

## Relatório

Abra Relatório completo e selecione `Cascade - Relatorio Remesh.json` no Editor de Texto. Os métodos são QUADWILD, QUADWILD_REFINED, EXACT e EXACT_FALLBACK; cada peça tem contagem, tolerância, medidas, tentativas e motivo de fallback. O último relatório também fica salvo na cena.

## Arquitetura / fontes

`remesh.py`: controlador modal, snapshot, GPU e aplicação reversível. `remesh_worker.py`: processo Blender isolado. `remesh_geometry.py`: guias, projeção limitada, conversão fiel, atributos e validação. `remesh_native.py`: interface C. `remesh_setup.py`: instalação verificável.

- [QuadWild - Reliable Feature-Line Driven Quad-Remeshing, SIGGRAPH 2021](https://github.com/nicopietroni/quadwild)
- [QuadWild + Bi-MDF](https://github.com/cgg-bern/quadwild-bimdf)
- [QRemeshify 1.1.0](https://github.com/ksami/QRemeshify/releases/tag/1.1.0), ABI/configurações adaptadas do commit e5e567958b34cfaed373a102bb7aa56454d642aa, GPL-3.0.

DLLs oficiais opcionais instaladas em `%LOCALAPPDATA%/CascadeCAD/remesh/quadwild-1.1.0`; URL e hashes fixos constam em remesh_setup.py e engine.json. Não há upload de modelos durante o processamento.

