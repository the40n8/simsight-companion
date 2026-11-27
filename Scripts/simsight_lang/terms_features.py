"""Travel list, phone, build cursor, keyboard shortcuts and later features."""

from __future__ import annotations

TERMS: dict[str, dict[str, str]] = {
    'travel list': {'en': 'Travel list', 'pl': 'Lista podróży'},
    'simplified menus': {'en': 'Simplified menus', 'pl': 'Uproszczone menu'},
    'option position': {'en': '{} of {}', 'pl': '{} z {}'},
    'selected count': {'en': '{} selected', 'pl': 'Wybrano: {}'},
    'phone list instructions': {
        'en': 'What the phone offers now, by category. Enter opens a category or runs an action. Escape closes.',
        'pl': 'To, co teraz oferuje telefon, według kategorii. Enter otwiera kategorię albo wykonuje działanie. Escape zamyka.',
    },
    'phone group instructions': {
        'en': 'Enter runs an action. The first row goes back to the phone. Escape closes.',
        'pl': 'Enter wykonuje działanie. Pierwszy wiersz wraca do telefonu. Escape zamyka.',
    },
    'phone group count': {'en': '{} actions', 'pl': 'Działania: {}'},
    'phone group count one': {'en': '{} action', 'pl': 'Działania: {}'},
    'back to phone': {'en': 'Back to the phone', 'pl': 'Wróć do telefonu'},
    'phone unavailable': {
        'en': 'The phone cannot be used right now.',
        'pl': 'Telefonu nie można teraz użyć.',
    },
    'phone empty': {
        'en': 'The phone has nothing to offer right now.',
        'pl': 'Telefon nie ma teraz nic do zaoferowania.',
    },
    'action queue full': {
        'en': 'The action queue is full.',
        'pl': 'Kolejka działań jest pełna.',
    },
    'travel': {'en': 'Travel', 'pl': 'Podróż'},
    'travel front instructions': {
        'en': 'Travel alone, or choose who comes along first. Escape closes.',
        'pl': 'Podróżuj sam albo najpierw wybierz, kto jedzie z tobą. Escape zamyka.',
    },
    'travel alone': {'en': 'Go alone', 'pl': 'Jedź sam'},
    'travel alone detail': {
        'en': 'Straight to the list of worlds and places.',
        'pl': 'Od razu do listy światów i miejsc.',
    },
    'travel with others': {'en': 'Choose who comes', 'pl': 'Wybierz, kto jedzie'},
    'travel with others detail': {
        'en': "The game's list of Sims who can come along, then the worlds and places.",
        'pl': 'Lista Simów z gry, którzy mogą jechać z tobą, potem światy i miejsca.',
    },
    'first person camera on': {
        'en': 'First-person camera on.',
        'pl': 'Kamera z perspektywy pierwszej osoby włączona.',
    },
    'first person camera off': {
        'en': 'First-person camera off.',
        'pl': 'Kamera z perspektywy pierwszej osoby wyłączona.',
    },
    'build controls': {'en': 'Build controls', 'pl': 'Sterowanie budowaniem'},
    'scanner mode off': {'en': 'Scanner mode off.', 'pl': 'Tryb skanera wyłączony.'},
    'scanner mode': {'en': 'Scanner mode', 'pl': 'Tryb skanera'},
    'scanner mode starts in live': {
        'en': 'Scanner mode on. It starts in Live mode.',
        'pl': 'Tryb skanera włączony. Zadziała w trybie życia.',
    },
    'scanner mode needs scanner': {
        'en': 'Turn on keyboard navigation and the scanner in the settings menu first.',
        'pl': 'Najpierw włącz nawigację klawiaturą i skaner w menu ustawień.',
    },
    'off in settings': {'en': 'off in settings', 'pl': 'wyłączone w ustawieniach'},
    'build actions': {'en': 'Build actions', 'pl': 'Działania budowania'},
    'build actions instructions': {
        'en': 'Choose a Build cursor action. After this window closes the cursor returns to where it was and the action runs. Escape closes.',
        'pl': 'Wybierz działanie kursora budowania. Po zamknięciu tego okna kursor wraca na swoje miejsce i działanie się wykonuje. Escape zamyka.',
    },
    'build cursor on': {'en': 'Build cursor on', 'pl': 'Włącz kursor budowania'},
    'build cursor off': {'en': 'Build cursor off', 'pl': 'Wyłącz kursor budowania'},
    'fine step': {'en': 'Fine step, 4 pixels', 'pl': 'Drobny krok, 4 piksele'},
    'coarse step': {'en': 'Coarse step, 16 or 64 pixels', 'pl': 'Duży krok, 16 lub 64 piksele'},
    'center cursor': {'en': 'Center the cursor', 'pl': 'Wyśrodkuj kursor'},
    'aim at grid point': {
        'en': 'Aim at the chosen lot grid point',
        'pl': 'Wyceluj w wybrany punkt siatki parceli',
    },
    'move left': {'en': 'Move left', 'pl': 'Przesuń w lewo'},
    'move right': {'en': 'Move right', 'pl': 'Przesuń w prawo'},
    'move up': {'en': 'Move up', 'pl': 'Przesuń w górę'},
    'move down': {'en': 'Move down', 'pl': 'Przesuń w dół'},
    'select or place': {'en': 'Select or place', 'pl': 'Wybierz lub postaw'},
    'cancel tool': {'en': 'Cancel the current tool', 'pl': 'Anuluj bieżące narzędzie'},
    'rotate left': {'en': 'Rotate left', 'pl': 'Obróć w lewo'},
    'rotate right': {'en': 'Rotate right', 'pl': 'Obróć w prawo'},
    'undo': {'en': 'Undo', 'pl': 'Cofnij'},
    'redo': {'en': 'Redo', 'pl': 'Ponów'},
    'cursor status': {'en': 'Cursor status', 'pl': 'Stan kursora'},
    'travel destinations': {'en': 'Travel', 'pl': 'Podróż'},
    'choose a world': {
        'en': 'Choose a world. Escape cancels the trip.',
        'pl': 'Wybierz świat. Escape anuluje podróż.',
    },
    'choose a place': {
        'en': 'Choose a place. Escape cancels the trip.',
        'pl': 'Wybierz miejsce. Escape anuluje podróż.',
    },
    'choose a unit': {
        'en': 'Choose a unit. Escape cancels the trip.',
        'pl': 'Wybierz lokal. Escape anuluje podróż.',
    },
    'places count': {'en': '{} places', 'pl': 'Miejsca: {}'},
    'units count': {'en': '{} units', 'pl': 'Lokale: {}'},
    'open the map instead': {'en': 'Open the map instead', 'pl': 'Otwórz zamiast tego mapę'},
    'back to worlds': {'en': 'Back to worlds', 'pl': 'Wróć do światów'},
    'back to places': {'en': 'Back to places', 'pl': 'Wróć do miejsc'},
    'other worlds': {'en': 'Other places', 'pl': 'Inne miejsca'},
    'travel cancelled': {'en': 'Travel cancelled.', 'pl': 'Podróż anulowana.'},
    'residents': {'en': 'Residents: {}', 'pl': 'Mieszkańcy: {}'},
    'price per day': {'en': '{} Simoleons per day', 'pl': '{} simoleonów za dzień'},
    'choose a stay': {
        'en': 'Choose how long to stay. Escape cancels the trip.',
        'pl': 'Wybierz długość pobytu. Escape anuluje podróż.',
    },
    'stay one day': {'en': '1 day, {} Simoleons', 'pl': '1 dzień, {} simoleonów'},
    'stay days': {'en': '{} days, {} Simoleons', 'pl': '{} dni, {} simoleonów'},
    'cannot afford': {'en': 'Not enough money.', 'pl': 'Za mało pieniędzy.'},
    'price changed': {
        'en': 'The price changed. Choose the stay again.',
        'pl': 'Cena się zmieniła. Wybierz pobyt ponownie.',
    },
    'map required here': {
        'en': 'This place asks which business to visit. Opening the map.',
        'pl': 'To miejsce wymaga wyboru firmy. Otwieram mapę.',
    },
    'keyboard shortcuts': {'en': 'Keyboard shortcuts', 'pl': 'Skróty klawiaturowe'},
    'shortcut household sim': {'en': 'control household Sim {}'},
    'shortcut shift tab': {
        'en': "Shift + Tab: previous control in menus, dialogs and the HUD; in the Live world with nothing open, the game's own first-person camera switch",
        'pl': 'Shift + Tab: poprzednia kontrolka w menu, oknach dialogowych i HUD; w świecie w trybie życia, gdy nic nie jest otwarte, to przełącznik kamery z perspektywy pierwszej osoby w grze',
    },
    'shortcut simplified': {
        'en': 'Simplified menus are on: Up and Down or Tab move one option at a time; Enter chooses an available single choice; '
        'Space ticks a multiple choice, then Done confirms; Escape goes back',
        'pl': 'Uproszczone menu są włączone: strzałki w górę i w dół albo Tab przechodzą o jedną opcję; Enter wybiera dostępną opcję pojedynczego wyboru; '
        'Spacja zaznacza opcję wielokrotnego wyboru, a potem Gotowe zatwierdza; Escape cofa',
    },
    'shortcut needs': {
        'en': 'Control + Shift + N: open Needs when no panel or text field owns focus'
    },
    'shortcut controls': {'en': 'UI controls'},
    'shortcut chords': {'en': 'Hold Control and Alt for these shortcuts'},
    'shortcut numpad': {'en': 'Optional numpad navigation is on. Use Num Lock'},
    'shortcut context': {
        'en': 'These are the mod shortcuts. Open panels and dialogs take priority over world navigation. '
        'In text fields, ordinary keys edit text. Household Sim shortcuts require no open panel or text field. '
        'World scanner shortcuts require the scanner setting. Grid and Build cursor modes change the arrow and action keys as described below'
    },
}

TERMS['scanner unavailable'] = {
    'en': 'unavailable',
    'pl': 'niedostępne',
    'de': 'nicht verfügbar',
    'fr': 'indisponible',
    'es': 'no disponible',
    'it': 'non disponibile',
    'pt': 'indisponível',
    'nl': 'niet beschikbaar',
    'ru': 'недоступно',
    'ja': '利用不可',
    'ko': '사용 불가',
    'zh': '無法使用',
    'sv': 'inte tillgänglig',
    'da': 'ikke tilgængelig',
    'fi': 'ei käytettävissä',
    'nb': 'utilgjengelig',
    'cs': 'nedostupné',
}


TERMS['photography controls'] = {'en': 'Photography controls', 'de': 'Fotosteuerung'}
TERMS['current event goals'] = {
    'en': 'Current event goals',
    'de': 'Aktuelle Veranstaltungsziele',
}
TERMS['event goals unavailable'] = {
    'en': 'Goals are not available for this event right now.',
    'de': 'Die Ziele dieser Veranstaltung sind gerade nicht verfügbar.',
}
