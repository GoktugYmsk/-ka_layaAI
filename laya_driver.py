import paho.mqtt.client as mqtt
import json
import time
import sys

# Klasör yolunu github reposuna göre ayarlıyoruz
sys.path.append('./laya')
import laya

# --- LAYA MODELİNİ YÜKLEME ---
print("Laya Multilingual Modeli indiriliyor/yükleniyor...")
print("Apple Silicon GPU (MPS) donanım hızlandırması kullanılıyor!")

# 100+ dili destekleyen multilingual (çok dilli) modeli MPS (Mac GPU) ile yüklüyoruz.
agent = laya.load("convaiinnovations/laya", subfolder="multilingual", device="mps")
print("Model Yüklendi ve Hazır!")

# --- AYARLAR ---
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
SISTEM_MODU = "MANUAL"
SON_KARAR_ZAMANI = 0
KARAR_GECIKMESI = 0.2  # Loglardaki akışa uygun olması için 0.2 saniye

# LM393 Enkoder Hafızası
ESKI_SOL = -1
ESKI_SAG = -1
SON_LAYA_KARARI = "DURUYOR"

def on_connect(client, userdata, flags, rc, properties=None):
    print("MQTT Broker'a Baglanildi. Laya AI Ajanı Aktif.")
    client.subscribe("ika/sistem/mod")
    client.subscribe("ika/telemetri")
    client.publish("ika/sistem/durum", json.dumps({"cihaz": "LAYA_AI", "durum": "ONLINE"}))

def on_message(client, userdata, msg):
    global SISTEM_MODU, SON_KARAR_ZAMANI, ESKI_SOL, ESKI_SAG, SON_LAYA_KARARI
    
    topic = msg.topic
    payload = msg.payload.decode('utf-8')
    
    if topic == "ika/sistem/mod":
        SISTEM_MODU = payload
        print(f"Sürüş Yetkisi Değişti: {SISTEM_MODU}")
        if SISTEM_MODU == "MANUAL":
            client.publish("ika/komut", json.dumps({"gaz": 0, "yon": 0}))
            # Manuel moda geçince hafızayı sıfırla ki tekrar otonoma alınca hatalı hareket etmesin
            ESKI_SOL = -1
            ESKI_SAG = -1
            SON_LAYA_KARARI = "DURUYOR"

    elif topic == "ika/telemetri" and SISTEM_MODU == "OTONOM":
        anlik_zaman = time.time()
        if (anlik_zaman - SON_KARAR_ZAMANI) > KARAR_GECIKMESI:
            SON_KARAR_ZAMANI = anlik_zaman
            try:
                telemetri = json.loads(payload)
                
                guncel_sol = telemetri.get("sol", 0)
                guncel_sag = telemetri.get("sag", 0)
                
                # İlk çalışma anında eski değerleri eşitle
                if ESKI_SOL == -1:
                    ESKI_SOL = guncel_sol
                    ESKI_SAG = guncel_sag
                    
                # Tekerleklerdeki hareketi (farkı) hesapla
                fark_sol = guncel_sol - ESKI_SOL
                fark_sag = guncel_sag - ESKI_SAG
                
                # Hafızayı bir sonraki döngü için güncelle
                ESKI_SOL = guncel_sol
                ESKI_SAG = guncel_sag

                print(f"Hareket Farkı -> Sol: +{fark_sol}, Sağ: +{fark_sag}")

                # --- ÇARPIŞMA/SIKIŞMA TESPİTİ ---
                # Eğer Laya bir önceki adımda "Gaz ver" dediyse ama tekerlekler dönemediyse (fark <= 1):
                if SON_LAYA_KARARI in ["ILERI", "SAGA_DON"] and (fark_sol <= 1 and fark_sag <= 1):
                    arac_durumu = "Fiziksel bir engele çarptım ve sıkıştım! Gaza basmama rağmen tekerlekler dönmüyor. Acilen geri kaçmalı ve yönümü değiştirmeliyim."
                else:
                    arac_durumu = "Önüm açık, tekerlekler sorunsuz bir şekilde dönüyor ve araç ilerliyor."

                prompt = {
                    "yon_secimi": {
                        "type": "choice",
                        "instructions": "Aracın LM393 tekerlek enkoderlerinden gelen fiziksel durumuna göre eylem seç.",
                        "criteria": {
                            "ILERI": "Tekerlekler dönüyorsa ve yol açıksa seçilir.",
                            "GERI_KAC": "Araç engele çarpıp sıkıştıysa (tekerlekler dönmüyorsa) kurtulmak için seçilir.",
                            "SAGA_DON": "Araç geri kaçtıktan sonra farklı bir rotaya girmek için seçilir."
                        }
                    }
                }
                
                baslangic_z = time.time()
                yanit = agent.predict(arac_durumu, prompt) 
                gecen_sure = (time.time() - baslangic_z) * 1000
                
                en_iyi_secenek = yanit["answers"]["yon_secimi"]["choice"]
                guven_skoru = yanit["answers"]["yon_secimi"]["confidence"]
                
                # Kararı hafızaya al ki bir sonraki döngüde sıkışıp sıkışmadığını bilsin
                SON_LAYA_KARARI = en_iyi_secenek
                
                print(f"Laya Gördü: {arac_durumu}")
                print(f"Laya Kararı: {en_iyi_secenek} (Güven: %{int(guven_skoru*100)}) - {gecen_sure:.1f} ms\n")
                
                # --- LOGLARA GÖRE GÜNCELLENMİŞ MOTOR KOMUTLARI (Maks 180) ---
                if en_iyi_secenek == "ILERI":
                    karar = {"gaz": 180, "yon": 0}
                elif en_iyi_secenek == "SAGA_DON":
                    karar = {"gaz": 180, "yon": 180}
                elif en_iyi_secenek == "GERI_KAC":
                    karar = {"gaz": -180, "yon": 0}
                else: 
                    karar = {"gaz": 0, "yon": 0}
                    
                client.publish("ika/komut", json.dumps(karar))
                
            except Exception as e:
                pass

# MQTT İstemci Kurulumu (Deprecation uyarısını çözen VERSION2 parametresi ile)
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="Laya_AI_Agent")
client.on_connect = on_connect
client.on_message = on_message
client.will_set("ika/sistem/durum", json.dumps({"cihaz": "LAYA_AI", "durum": "OFFLINE"}), qos=1, retain=False)

try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    print("Laya Ajanı Başlatılıyor... React HMI'dan OTONOM yetkisi bekleniyor.")
    client.loop_forever()
except KeyboardInterrupt:
    print("\nLaya Ajanı Kapatıldı.")
    client.publish("ika/sistem/durum", json.dumps({"cihaz": "LAYA_AI", "durum": "OFFLINE"}))
    client.disconnect()