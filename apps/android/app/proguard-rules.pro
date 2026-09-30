# R8 / ProGuard rules for the Fairwind Android client (skeleton).
#
# Spec 88-90, 132. Keep this file deliberately small: no rule is added for a library
# that has not actually been linked in a build, because an unused keep-rule is a lie
# about what the app needs.
#
# Not verified: no release build has ever been run here (no SDK/JDK).

# Keep the service and activity entry points referenced only from the manifest.
-keep class club.noclub.accelerator.vpn.AcceleratorVpnService { *; }
-keep class club.noclub.accelerator.MainActivity { *; }

# The core adapter models are (de)serialised field-by-field by the config generator's
# JSON writer, which is pure Kotlin - no reflection is used, so no keep-rule is needed.
# TODO(v0.5): revisit once the proxy core is chosen (docs/CORE_APPROVAL.md).

# Strip verbose/debug logs from release builds; secrets must never be logged at all
# (docs/SECURITY.md §7). This is belt-and-braces, not the only redaction layer.
-assumenosideeffects class android.util.Log {
    public static *** v(...);
    public static *** d(...);
}
