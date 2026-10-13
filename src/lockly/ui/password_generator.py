"""Password / passphrase generator dialog."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ..core import generator
from ..core.generator import PassphraseOptions, PasswordOptions
from ..core.settings import Settings

SEPARATORS = [("-", "Hyphen  -"), (".", "Period  ."), ("_", "Underscore  _"), (" ", "Space"), (",", "Comma  ,")]


class GeneratorDialog(Adw.Dialog):
    def __init__(
        self,
        settings: Settings,
        on_copy: Callable[[str], None],
        on_use: Callable[[str], None] | None = None,
    ):
        super().__init__(title="Password Generator", content_width=460, content_height=640)
        self.on_copy = on_copy
        self.on_use = on_use
        self.password_options = PasswordOptions(**settings.password.to_dict())
        self.passphrase_options = PassphraseOptions(**settings.passphrase.to_dict())
        self.mode = settings.generator_mode
        self._value = ""

        header = Adw.HeaderBar()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)

        # Output
        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        output_box.add_css_class("card")
        self.output = Gtk.Label(wrap=True, selectable=True, xalign=0)
        self.output.set_wrap_mode(2)
        self.output.add_css_class("generated-password")
        self.output.update_property([Gtk.AccessibleProperty.LABEL], ["Generated password"])
        output_box.append(self.output)
        body.append(output_box)

        self.entropy_label = Gtk.Label(xalign=0)
        self.entropy_label.add_css_class("caption")
        self.entropy_label.add_css_class("dim-label")
        body.append(self.entropy_label)

        actions = Gtk.Box(spacing=8, homogeneous=False)
        regen = Gtk.Button(tooltip_text="Generate a new password")
        regen.set_child(Adw.ButtonContent(icon_name="view-refresh-symbolic", label="Regenerate"))
        regen.connect("clicked", lambda *_: self.regenerate())
        copy = Gtk.Button()
        copy.set_child(Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Copy"))
        copy.connect("clicked", lambda *_: self.on_copy(self._value) if self._value else None)
        actions.append(regen)
        actions.append(copy)
        if on_use is not None:
            use = Gtk.Button(label="Use Password", hexpand=True, halign=Gtk.Align.END)
            use.add_css_class("suggested-action")
            use.connect("clicked", lambda *_: self._use())
            actions.append(use)
            self.set_default_widget(use)
        body.append(actions)

        # Mode switch (linked toggle buttons work on every libadwaita >= 1.6)
        mode_box = Gtk.Box(halign=Gtk.Align.CENTER)
        mode_box.add_css_class("linked")
        self.password_toggle = Gtk.ToggleButton(label="Password", active=self.mode == "password")
        self.passphrase_toggle = Gtk.ToggleButton(label="Passphrase", group=self.password_toggle, active=self.mode == "passphrase")
        for button in (self.password_toggle, self.passphrase_toggle):
            button.connect("toggled", self._on_mode)
            mode_box.append(button)
        body.append(mode_box)

        # Password options
        self.password_group = Adw.PreferencesGroup()
        self.length_row = Adw.SpinRow.new_with_range(generator.MIN_LENGTH, generator.MAX_LENGTH, 1)
        self.length_row.set_title("Length")
        self.length_row.set_value(self.password_options.length)
        self.length_row.connect("notify::value", self._on_option)
        self.password_group.add(self.length_row)
        self.switches: dict[str, Adw.SwitchRow] = {}
        for key, title, subtitle in (
            ("uppercase", "Uppercase Letters", "A–Z"),
            ("lowercase", "Lowercase Letters", "a–z"),
            ("digits", "Numbers", "0–9"),
            ("symbols", "Symbols", "!#$%&amp;*…"),
            ("exclude_ambiguous", "Avoid Ambiguous Characters", "Leave out I, l, 1, O, 0 and similar"),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=getattr(self.password_options, key))
            row.connect("notify::active", self._on_option)
            self.switches[key] = row
            self.password_group.add(row)
        body.append(self.password_group)

        # Passphrase options
        self.passphrase_group = Adw.PreferencesGroup()
        self.words_row = Adw.SpinRow.new_with_range(generator.MIN_WORDS, generator.MAX_WORDS, 1)
        self.words_row.set_title("Words")
        self.words_row.set_value(self.passphrase_options.words)
        self.words_row.connect("notify::value", self._on_option)
        self.separator_row = Adw.ComboRow(title="Separator", model=Gtk.StringList.new([label for _, label in SEPARATORS]))
        separators = [s for s, _ in SEPARATORS]
        if self.passphrase_options.separator in separators:
            self.separator_row.set_selected(separators.index(self.passphrase_options.separator))
        self.separator_row.connect("notify::selected", self._on_option)
        self.capitalize_row = Adw.SwitchRow(title="Capitalize Words", active=self.passphrase_options.capitalize)
        self.capitalize_row.connect("notify::active", self._on_option)
        self.number_row = Adw.SwitchRow(title="Include a Number", active=self.passphrase_options.include_number)
        self.number_row.connect("notify::active", self._on_option)
        for row in (self.words_row, self.separator_row, self.capitalize_row, self.number_row):
            self.passphrase_group.add(row)
        note = Gtk.Label(
            label="Words come from the EFF large wordlist (7,776 words).",
            xalign=0, wrap=True, margin_top=8,
        )
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        self.passphrase_group.add(note)
        body.append(self.passphrase_group)

        clamp = Adw.Clamp(maximum_size=520, child=body)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_child(view)
        self._sync_mode()
        self.regenerate()
        self.connect("closed", lambda *_: self._forget())

    def _forget(self) -> None:
        self._value = ""
        self.output.set_label("")

    def _on_mode(self, button: Gtk.ToggleButton) -> None:
        if not button.get_active():
            return
        self.mode = "passphrase" if self.passphrase_toggle.get_active() else "password"
        self._sync_mode()
        self.regenerate()

    def _sync_mode(self) -> None:
        self.password_group.set_visible(self.mode == "password")
        self.passphrase_group.set_visible(self.mode == "passphrase")

    def _on_option(self, *_args) -> None:
        self.password_options.length = int(self.length_row.get_value())
        for key, row in self.switches.items():
            setattr(self.password_options, key, row.get_active())
        self.passphrase_options.words = int(self.words_row.get_value())
        self.passphrase_options.separator = SEPARATORS[self.separator_row.get_selected()][0]
        self.passphrase_options.capitalize = self.capitalize_row.get_active()
        self.passphrase_options.include_number = self.number_row.get_active()
        self.regenerate()

    def regenerate(self) -> None:
        try:
            if self.mode == "password":
                self._value = generator.generate_password(self.password_options)
                bits = generator.password_entropy(self.password_options)
            else:
                self._value = generator.generate_passphrase(self.passphrase_options)
                bits = generator.passphrase_entropy(self.passphrase_options)
        except ValueError as exc:
            self._value = ""
            self.output.set_label("")
            self.entropy_label.set_label(str(exc))
            return
        self.output.set_label(self._value)
        self.entropy_label.set_label(f"About {bits:.0f} bits of entropy")

    def _use(self) -> None:
        if self._value and self.on_use is not None:
            self.on_use(self._value)
        self.close()
