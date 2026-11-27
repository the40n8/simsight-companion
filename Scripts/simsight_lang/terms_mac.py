"""Terms whose wording differs on the Mac (other keys)."""

from __future__ import annotations

# macOS: different keys (Option+Command+A opens the accessibility menu).
TERMS: dict[str, dict[str, str]] = {
    'screen reader installed': {
        'en': 'SimSight is installed. Option Command A opens the accessibility menu.',
        'fr': "SimSight est installé. Option Commande A ouvre le menu d'accessibilité.",
        'es': 'SimSight está instalado. Opción Comando A abre el menú de accesibilidad.',
        'de': 'SimSight ist installiert. Wahl Befehl A öffnet das Bedienungshilfen-Menü.',
        'it': 'SimSight è installato. Opzione Comando A apre il menu di accessibilità.',
        'pt': 'SimSight está instalado. Opção Comando A abre o menu de acessibilidade.',
    },
}
