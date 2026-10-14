# SASTRA uses no reflection-based serialization (org.json only), so default
# R8 rules are sufficient. Keep WorkManager workers instantiated by class name.
-keep class com.sastra.app.work.** { *; }
