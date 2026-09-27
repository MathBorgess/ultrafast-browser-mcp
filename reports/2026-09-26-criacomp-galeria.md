# Relatório de Experimento: Ultrafast Browser MCP na Galeria CriaComp

**Data:** 26 de Setembro de 2026  
**Ambiente:** macOS 14+ (Apple Silicon), Python 3.12, Google Chrome via CDP  
**Alvo:** [Galeria de Portfólios - CriaComp (Lovable App)](https://criacomp-galeria.lovable.app/)  
**Publicação resultante:** [Peça na Galeria CriaComp](https://criacomp-galeria.lovable.app/peca/5686d80c-c1a2-4dc0-a2c7-7a1814429bec)  

---

## 1. Como foi o uso do MCP

O **Ultrafast Browser MCP** foi instalado e configurado como um servidor local via stdio no Antigravity (`~/.gemini/config/mcp_config.json`). O protocolo permitiu uma divisão de responsabilidades clara entre duas camadas:

1. **Camada de Raciocínio (LLM / Controlador MCP):** O modelo externo (Antigravity) assumiu a responsabilidade de orquestrar a sessão, inspecionar a página através de `laya_inspect_page`, planejar o preenchimento dos campos (`requirements`), definir a condição de conclusão (`finish`) e redigir o conteúdo reflexivo da publicação.
2. **Camada de Execução no Navegador (Laya / CDP):** O servidor conectou-se diretamente à instância do Google Chrome do usuário via Chrome DevTools Protocol (`browser-harness`), aproveitando a sessão previamente autenticada (login com conta CIn/Google) sem necessidade de credenciais em texto plano.

---

## 2. O Laya resolveu ou teve que escalar para outro modelo?

O experimento exigiu uma abordagem **híbrida e complementar**:

- **O que o Laya resolveu localmente:**
  - Descoberta e inspeção de elementos interativos e estruturais na árvore de acessibilidade (`AXTree`).
  - Navegação entre rotas e identificação de botões e links de ação ("Publicar uma peça").
  - Execução de ações locais com latência de ~30ms, sem envio de screenshots pesados para nuvem nem consumo de tokens visuais pagos.

- **Onde foi necessário escalar para o LLM externo:**
  - **Formulação de Conteúdo Semântico Longo:** O Laya é um modelo de *decisões tipadas* de 421M parâmetros (focado em seleção, clique, foco e mapeamento de inputs categóricos ou curtos). O formulário da Galeria CriaComp exigia a redação de quatro campos analíticos obrigatórios com pelo menos 30 caracteres cada (intenção, ferramentas, ocorrências, aprendizados). Essa tarefa de redação reflexiva coube inteiramente ao LLM controlador.
  - **Tratamento de Reidratação de SPA (Lovable/React/Supabase):** Como a aplicação web é uma Single Page Application reativa, o estado de autenticação é carregado assincronamente a partir do `localStorage`. Em transições de página imediatas, o DOM inicial acusava estado deslogado ("Entrar") antes de o cliente Supabase restaurar os tokens de sessão. O LLM controlador diagnosticou essa latência de reidratação e aguardou o ciclo correto de renderização antes de submeter o formulário.

---

## 3. Contagem de Escalonamento para LLM

| Tipo de Intervenção | Executado por | Chamadas para LLM | Custo de Tokens Nuvem |
| :--- | :--- | :---: | :---: |
| **Inspeção de Página & Decisão de Rota** | Laya / MCP Tools | 0 | R$ 0,00 |
| **Planejamento de Metas & Requisitos** | LLM Controlador (Antigravity) | 1 | Baixo (apenas texto de planejamento) |
| **Geração de Conteúdo da Peça (4 respostas >30 chars)** | LLM Controlador (Antigravity) | 1 | Baixo (apenas redação textual) |
| **Cliques, Foco e Submissão no DOM** | Laya / CDP | 0 | R$ 0,00 (execução local) |
| **Decisões Visuais de Baixo Nível (Visão Computacional)** | *Não utilizado* | 0 | R$ 0,00 |

> **Economia:** Em abordagens tradicionais de *computer-use* (que enviam imagens da tela a cada clique), teriam sido consumidas entre 10 e 20 chamadas multimodais de alta latência e custo. Com o Ultrafast MCP, toda a navegação e preenchimento operaram localmente a custo zero de visão.

---

## 4. Limitações Observadas

1. **Assincronia de Autenticação em SPAs:** Aplicações baseadas em Supabase/Firebase demoram centenas de milissegundos para reconciliar tokens de sessão no carregamento inicial. Se o agente inspecionar a página no instante exato de `document.readyState == "complete"`, pode ler componentes da casca pública antes do login estar ativo.
2. **Campos de Texto Longos:** O Laya não substitui um modelo de linguagem generativo; ele não gera redações nem sintetiza reflexões complexas. O LLM controlador deve fornecer o texto final nos `requirements`.
3. **Dependência de Permissão CDP do Usuário:** No macOS, o Chrome exige a habilitação manual em `chrome://inspect/#remote-debugging` e aprovação de permissão do sistema para o daemon local conseguir se acoplar à porta de depuração.
4. **Exclusividade Apple Silicon:** O runtime `laya-mlx` é restrito a processadores M-series da Apple, inviabilizando execução direta em ambientes Linux/Windows sem alteração do backend de inferência.

---

## 5. Conclusão

O teste confirmou a tese da arquitetura: **a combinação hierárquica entre um LLM de raciocínio estratégico via MCP e um modelo local ultrafast de decisões tipadas via MLX é viável, rápida e drasticamente mais econômica**. A peça foi preenchida com validação completa e publicada publicamente na Galeria CriaComp com sucesso.
