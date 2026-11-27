"""Return types checked by hand in the game's bytecode for the core modules
(menu, speech, scanner, narrator, keys, ...). Same format as
tools/ea_overlay.py."""

RETURNS = {
    # bool(self._ui_metadata_handles): the tooltip has metadata of its own.
    'objects.components.tooltip_component.TooltipComponent.has_ui_metadata_handles': 'bool',
    # _zone_manager.current_zone, or None before the zone manager exists.
    'services.current_zone': 'Zone | None',
    # current_zone().ui_dialog_service: AttributeError, not None, without a zone.
    'services.ui_dialog_service': 'UiDialogService',
}
