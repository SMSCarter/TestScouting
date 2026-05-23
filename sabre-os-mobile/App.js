import React, { useState, useEffect, useRef } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TextInput,
  TouchableOpacity,
  ScrollView,
  SafeAreaView,
  StatusBar,
  ActivityIndicator,
  Modal,
  Platform,
  Alert
} from 'react-native';
import * as SQLite from 'expo-sqlite';
import NetInfo from '@react-native-community/netinfo';
import axios from 'axios';
import Logo from './src/components/Logo';

export default function App() {
  // App state
  const [db, setDb] = useState(null);
  const [isConnected, setIsConnected] = useState(true);
  const [serverIp, setServerIp] = useState('127.0.0.1'); // Will auto-detect or allow customization
  const [isIpModalOpen, setIsIpModalOpen] = useState(false);
  const [ipInput, setIpInput] = useState('127.0.0.1');

  // Form Fields
  const [teamNumber, setTeamNumber] = useState('');
  const [matchNumber, setMatchNumber] = useState('');
  const [autoScore, setAutoScore] = useState(0);
  const [teleopCycles, setTeleopCycles] = useState(0);
  const [scoutInitials, setScoutInitials] = useState('');
  const [endgameStatus, setEndgameStatus] = useState('None');

  // Status & Data
  const [localMatches, setLocalMatches] = useState([]);
  const [unsyncedCount, setUnsyncedCount] = useState(0);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncTime, setLastSyncTime] = useState(null);

  // Database initialization
  useEffect(() => {
    async function initDb() {
      try {
        const database = await SQLite.openDatabaseAsync('sabreos.db');
        await database.execAsync(`
          PRAGMA journal_mode = WAL;
          CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_number TEXT NOT NULL,
            match_number TEXT NOT NULL,
            auto_score INTEGER NOT NULL,
            teleop_cycles INTEGER NOT NULL,
            scout_initials TEXT DEFAULT '',
            endgame_status TEXT DEFAULT 'None',
            synced INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
          );
        `);
        // Apply migrations safely for existing databases
        try {
          await database.execAsync("ALTER TABLE matches ADD COLUMN scout_initials TEXT DEFAULT '';");
        } catch(e) {}
        try {
          await database.execAsync("ALTER TABLE matches ADD COLUMN endgame_status TEXT DEFAULT 'None';");
        } catch(e) {}
        setDb(database);
        await loadMatches(database);
      } catch (err) {
        console.error('Error opening database:', err);
      }
    }

    // Auto detect local IP on web or simulator
    if (Platform.OS === 'android') {
      setServerIp('10.0.2.2'); // Default Android emulator host address
      setIpInput('10.0.2.2');
    } else {
      setServerIp('127.0.0.1'); // iOS / web
      setIpInput('127.0.0.1');
    }

    initDb();
  }, []);

  // Monitor network connection
  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener((state) => {
      // NetInfo can report null sometimes, treat as disconnected if null
      const status = !!state.isConnected;
      setIsConnected(status);
    });
    return () => unsubscribe();
  }, []);

  // Load matches from local SQLite
  const loadMatches = async (database = db) => {
    if (!database) return;
    try {
      const records = await database.getAllAsync(
        'SELECT * FROM matches ORDER BY id DESC LIMIT 20'
      );
      setLocalMatches(records);

      const unsynced = await database.getFirstAsync(
        'SELECT COUNT(*) as count FROM matches WHERE synced = 0'
      );
      setUnsyncedCount(unsynced?.count || 0);
    } catch (err) {
      console.error('Error loading matches:', err);
    }
  };

  // Helper to parse input IP/Port and route to FastAPI backend (Port 8000)
  const getBaseUrl = (inputIp) => {
    let clean = inputIp.trim().replace(/^https?:\/\//, '');
    if (clean.includes(':')) {
      const [host, port] = clean.split(':');
      if (port === '8081' || port === '8082' || port === '8083') {
        // Redirect from Metro Bundler port to FastAPI Backend port
        return `http://${host}:8000`;
      }
      return `http://${clean}`;
    }
    return `http://${clean}:8000`;
  };

  // Perform backend Sync
  const syncMatches = async (database = db, ip = serverIp) => {
    if (!database || isSyncing) return;
    setIsSyncing(true);
    try {
      const unsynced = await database.getAllAsync(
        'SELECT * FROM matches WHERE synced = 0'
      );
      if (unsynced.length === 0) {
        setIsSyncing(false);
        return;
      }

      const baseUrl = getBaseUrl(ip);
      console.log(`Syncing ${unsynced.length} records to ${baseUrl}/sync`);

      const response = await axios.post(
        `${baseUrl}/sync`,
        {
          records: unsynced.map((r) => ({
            team_number: r.team_number,
            match_number: r.match_number,
            auto_score: r.auto_score,
            teleop_cycles: r.teleop_cycles,
            scout_initials: r.scout_initials || '',
            endgame_status: r.endgame_status || 'None',
            created_at: r.created_at
          }))
        },
        { timeout: 5000 }
      );

      if (response.status === 200) {
        // Mark all unsynced records as synced in local SQLite
        for (const record of unsynced) {
          await database.runAsync('UPDATE matches SET synced = 1 WHERE id = ?', [
            record.id
          ]);
        }
        setLastSyncTime(new Date().toLocaleTimeString());
        console.log('Sync complete!');
      }
    } catch (err) {
      console.warn('Sync failed (will retry):', err.message);
    } finally {
      setIsSyncing(false);
      await loadMatches(database);
    }
  };

  // Lobby Sync Background Listener (checks every 10 seconds if unsynced data exists)
  useEffect(() => {
    const interval = setInterval(() => {
      if (unsyncedCount > 0 && db) {
        syncMatches(db, serverIp);
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [unsyncedCount, db, serverIp]);

  // Sync immediately when connection becomes available or on mount
  useEffect(() => {
    if (unsyncedCount > 0 && db) {
      syncMatches(db, serverIp);
    }
  }, [isConnected, unsyncedCount, db]);

  // Handle Form Submission (Offline-First)
  const handleSubmit = async () => {
    if (!scoutInitials.trim()) {
      Alert.alert('Missing Field', 'Please enter your Scout Initials');
      return;
    }
    if (!teamNumber.trim()) {
      Alert.alert('Missing Field', 'Please enter a Team Number');
      return;
    }
    if (!matchNumber.trim()) {
      Alert.alert('Missing Field', 'Please enter a Match Number');
      return;
    }
    if (!db) {
      Alert.alert('Database Error', 'Local database is not initialized');
      return;
    }

    try {
      // Save directly to offline database
      await db.runAsync(
        'INSERT INTO matches (team_number, match_number, auto_score, teleop_cycles, scout_initials, endgame_status, synced) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [teamNumber.trim(), matchNumber.trim(), autoScore, teleopCycles, scoutInitials.trim().toUpperCase(), endgameStatus, 0]
      );

      Alert.alert(
        'Success',
        `Match ${matchNumber} for Team ${teamNumber} saved locally!`,
        [{ text: 'OK' }],
        { cancelable: true }
      );

      // Save the submitted match number for incrementing
      const nextMatch = parseInt(matchNumber, 10) + 1;

      // Reset Form fields (keep initials sticky!)
      setTeamNumber('');
      setMatchNumber(isNaN(nextMatch) ? '' : String(nextMatch));
      setAutoScore(0);
      setTeleopCycles(0);
      setEndgameStatus('None');

      // Reload local records
      await loadMatches(db);
    } catch (err) {
      console.error('Error saving match:', err);
      Alert.alert('Error', 'Failed to save match data locally');
    }
  };

  const handleIpSave = () => {
    let clean = ipInput.trim();
    if (clean.includes(':')) {
      const [host, port] = clean.split(':');
      if (port === '8081' || port === '8082' || port === '8083') {
        Alert.alert(
          'Port Auto-Correction 🔄',
          `We noticed you entered port ${port} (which is your Expo Metro Bundler port for loading the UI).\n\nWe have automatically redirected your data sync requests to the FastAPI Backend Server on port 8000!`,
          [{ text: 'Got it!' }]
        );
        setServerIp(host);
        setIpInput(host);
        setIsIpModalOpen(false);
        if (db) syncMatches(db, host);
        return;
      }
    }

    setServerIp(ipInput);
    setIsIpModalOpen(false);
    if (db) syncMatches(db, ipInput);
  };

  return (
    <SafeAreaView style={styles.safeContainer}>
      <StatusBar barStyle="light-content" backgroundColor="#0B0F19" />
      <View style={styles.header}>
        <Logo size={55} />
        <View style={styles.headerTextContainer}>
          <Text style={styles.titleText}>SabreOS</Text>
          <Text style={styles.subtitleText}>FRC Scouting MVP</Text>
        </View>

        {/* IP Setting Toggle */}
        <TouchableOpacity
          onPress={() => {
            setIpInput(serverIp);
            setIsIpModalOpen(true);
          }}
          style={styles.settingsButton}
        >
          <Text style={styles.settingsButtonText}>⚙️ IP</Text>
        </TouchableOpacity>
      </View>

      {/* Network Status bar */}
      <View
        style={[
          styles.statusBar,
          isConnected ? styles.statusOnline : styles.statusOffline
        ]}
      >
        <View style={styles.statusDotRow}>
          <View
            style={[
              styles.statusDot,
              isConnected ? styles.dotOnline : styles.dotOffline
            ]}
          />
          <Text style={styles.statusText}>
            {isConnected ? 'ONLINE' : 'OFFLINE (SAVING LOCAL)'}
          </Text>
        </View>
        <Text style={styles.queueText}>
          {unsyncedCount === 0
            ? 'All synced'
            : `${unsyncedCount} match${unsyncedCount > 1 ? 'es' : ''} in queue`}
        </Text>
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* Core Scouting Form */}
        <View style={styles.card}>
          <Text style={styles.cardHeader}>Match Details</Text>

          <View style={styles.row}>
            <View style={styles.col}>
              <Text style={styles.inputLabel}>TEAM NUMBER</Text>
              <TextInput
                style={styles.input}
                placeholder="e.g. 6045"
                placeholderTextColor="#64748B"
                keyboardType="numeric"
                value={teamNumber}
                onChangeText={setTeamNumber}
              />
            </View>
            <View style={styles.col}>
              <Text style={styles.inputLabel}>MATCH NUMBER</Text>
              <TextInput
                style={styles.input}
                placeholder="e.g. 12"
                placeholderTextColor="#64748B"
                keyboardType="numeric"
                value={matchNumber}
                onChangeText={setMatchNumber}
              />
            </View>
          </View>

          <View style={[styles.row, { marginTop: 12 }]}>
            <View style={styles.col}>
              <Text style={styles.inputLabel}>SCOUT INITIALS (STICKY 📌)</Text>
              <TextInput
                style={styles.input}
                placeholder="e.g. SMS"
                placeholderTextColor="#64748B"
                autoCapitalize="characters"
                maxLength={4}
                value={scoutInitials}
                onChangeText={(val) => setScoutInitials(val.toUpperCase())}
              />
            </View>
          </View>
        </View>

        {/* Autonomous Section */}
        <View style={styles.card}>
          <Text style={styles.cardHeaderSection}>🤖 Autonomous Scores</Text>
          <Text style={styles.sectionDesc}>Auto Points Scored</Text>

          <View style={styles.stepperContainer}>
            <TouchableOpacity
              activeOpacity={0.7}
              style={[styles.stepperBtn, styles.btnMinus]}
              onPress={() => setAutoScore(Math.max(0, autoScore - 1))}
            >
              <Text style={styles.stepperBtnText}>-</Text>
            </TouchableOpacity>

            <Text style={styles.stepperValue}>{autoScore}</Text>

            <TouchableOpacity
              activeOpacity={0.7}
              style={[styles.stepperBtn, styles.btnPlus]}
              onPress={() => setAutoScore(autoScore + 1)}
            >
              <Text style={styles.stepperBtnText}>+</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Teleop Section */}
        <View style={styles.card}>
          <Text style={styles.cardHeaderSection}>⚡ Teleop Cycles</Text>
          <Text style={styles.sectionDesc}>Successful scoring runs</Text>

          <View style={styles.stepperContainer}>
            <TouchableOpacity
              activeOpacity={0.7}
              style={[styles.stepperBtn, styles.btnMinus]}
              onPress={() => setTeleopCycles(Math.max(0, teleopCycles - 1))}
            >
              <Text style={styles.stepperBtnText}>-</Text>
            </TouchableOpacity>

            <Text style={styles.stepperValue}>{teleopCycles}</Text>

            <TouchableOpacity
              activeOpacity={0.7}
              style={[styles.stepperBtn, styles.btnPlus]}
              onPress={() => setTeleopCycles(teleopCycles + 1)}
            >
              <Text style={styles.stepperBtnText}>+</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Endgame Section */}
        <View style={styles.card}>
          <Text style={styles.cardHeaderSection}>🧗 Endgame Climbing</Text>
          <Text style={styles.sectionDesc}>Select robot climbing status</Text>

          <View style={styles.buttonGroup}>
            {['None', 'Parked', 'Climbed', 'Harmony'].map((status) => {
              const points = status === 'Parked' ? ' (+2p)' : status === 'Climbed' ? ' (+3p)' : status === 'Harmony' ? ' (+4p)' : '';
              const isSelected = endgameStatus === status;
              return (
                <TouchableOpacity
                  key={status}
                  activeOpacity={0.7}
                  style={[
                    styles.groupBtn,
                    isSelected ? styles.groupBtnSelected : styles.groupBtnUnselected
                  ]}
                  onPress={() => setEndgameStatus(status)}
                >
                  <Text style={[
                    styles.groupBtnText,
                    isSelected ? styles.groupBtnTextSelected : styles.groupBtnTextUnselected
                  ]}>
                    {status}{points}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        {/* Submit Action */}
        <TouchableOpacity
          activeOpacity={0.8}
          style={styles.submitButton}
          onPress={handleSubmit}
        >
          <Text style={styles.submitBtnText}>💾 SUBMIT RECORD</Text>
        </TouchableOpacity>

        {/* Recent Matches Panel */}
        <View style={styles.historyCard}>
          <View style={styles.historyHeader}>
            <Text style={styles.historyTitle}>Recent Local Records</Text>
            {unsyncedCount > 0 && (
              <TouchableOpacity
                onPress={() => syncMatches(db, serverIp)}
                disabled={isSyncing}
                style={styles.manualSyncBtn}
              >
                {isSyncing ? (
                  <ActivityIndicator size="small" color="#FFFFFF" />
                ) : (
                  <Text style={styles.manualSyncText}>Sync Now</Text>
                )}
              </TouchableOpacity>
            )}
          </View>

          {lastSyncTime && (
            <Text style={styles.lastSyncText}>Last successful sync: {lastSyncTime}</Text>
          )}

          {localMatches.length === 0 ? (
            <Text style={styles.noDataText}>No records scouted yet.</Text>
          ) : (
            localMatches.map((item) => (
              <View key={item.id} style={styles.historyItem}>
                <View>
                  <Text style={styles.historyItemTitle}>
                    Match {item.match_number} | Team {item.team_number}
                  </Text>
                  <Text style={styles.historyItemDetail}>
                    Scout: {item.scout_initials || 'N/A'} • Auto: {item.auto_score} pts • Teleop: {item.teleop_cycles} cycles • Climb: {item.endgame_status || 'None'}
                  </Text>
                </View>
                <View
                  style={[
                    styles.syncBadge,
                    item.synced === 1 ? styles.badgeSynced : styles.badgePending
                  ]}
                >
                  <Text style={styles.badgeText}>
                    {item.synced === 1 ? 'Synced ✓' : 'Queue ⏳'}
                  </Text>
                </View>
              </View>
            ))
          )}
        </View>
      </ScrollView>

      {/* IP Configuration Modal */}
      <Modal
        visible={isIpModalOpen}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setIsIpModalOpen(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <Text style={styles.modalTitle}>Configure API Server</Text>
            <Text style={styles.modalDesc}>
              Enter the IP address of your FastAPI backend. Set to 127.0.0.1 for
              iOS Simulator or 10.0.2.2 for Android. For physical devices, enter
              your Mac's Wi-Fi IP address.
            </Text>

            <TextInput
              style={styles.modalInput}
              value={ipInput}
              onChangeText={setIpInput}
              placeholder="e.g. 192.168.1.50"
              placeholderTextColor="#64748B"
            />

            <View style={styles.modalBtnRow}>
              <TouchableOpacity
                onPress={() => setIsIpModalOpen(false)}
                style={[styles.modalBtn, styles.modalBtnCancel]}
              >
                <Text style={styles.modalBtnText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                onPress={handleIpSave}
                style={[styles.modalBtn, styles.modalBtnSave]}
              >
                <Text style={styles.modalBtnText}>Save & Sync</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeContainer: {
    flex: 1,
    backgroundColor: '#0B0F19',
    paddingTop: Platform.OS === 'android' ? StatusBar.currentHeight : 0
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
    backgroundColor: '#0F172A'
  },
  headerTextContainer: {
    marginLeft: 12,
    flex: 1
  },
  titleText: {
    color: '#FFFFFF',
    fontSize: 22,
    fontWeight: '900',
    letterSpacing: 0.5
  },
  subtitleText: {
    color: '#60A5FA',
    fontSize: 12,
    fontWeight: '600'
  },
  settingsButton: {
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155'
  },
  settingsButtonText: {
    color: '#F8FAFC',
    fontWeight: '700',
    fontSize: 12
  },
  statusBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 8
  },
  statusOnline: {
    backgroundColor: 'rgba(16, 185, 129, 0.1)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(16, 185, 129, 0.2)'
  },
  statusOffline: {
    backgroundColor: 'rgba(245, 158, 11, 0.1)',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(245, 158, 11, 0.2)'
  },
  statusDotRow: {
    flexDirection: 'row',
    alignItems: 'center'
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 8
  },
  dotOnline: {
    backgroundColor: '#10B981'
  },
  dotOffline: {
    backgroundColor: '#F59E0B'
  },
  statusText: {
    color: '#F8FAFC',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5
  },
  queueText: {
    color: '#94A3B8',
    fontSize: 11,
    fontWeight: '600'
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 40
  },
  card: {
    backgroundColor: '#151F32',
    borderRadius: 16,
    padding: 18,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#1E293B',
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 6,
    elevation: 4
  },
  cardHeader: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '800',
    marginBottom: 14,
    letterSpacing: 0.5,
    textTransform: 'uppercase'
  },
  cardHeaderSection: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '800',
    letterSpacing: 0.5
  },
  sectionDesc: {
    color: '#94A3B8',
    fontSize: 12,
    marginTop: 2,
    marginBottom: 16
  },
  row: {
    flexDirection: 'row',
    marginHorizontal: -8
  },
  col: {
    flex: 1,
    paddingHorizontal: 8
  },
  inputLabel: {
    color: '#60A5FA',
    fontSize: 10,
    fontWeight: '800',
    marginBottom: 6,
    letterSpacing: 1
  },
  input: {
    backgroundColor: '#0F172A',
    borderRadius: 10,
    color: '#FFFFFF',
    paddingVertical: 12,
    paddingHorizontal: 16,
    fontSize: 18,
    fontWeight: '700',
    borderWidth: 1.5,
    borderColor: '#334155'
  },
  stepperContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#0F172A',
    borderRadius: 12,
    padding: 6,
    borderWidth: 1,
    borderColor: '#1E293B'
  },
  stepperBtn: {
    width: 60,
    height: 60,
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center'
  },
  btnMinus: {
    backgroundColor: '#EF4444'
  },
  btnPlus: {
    backgroundColor: '#10B981'
  },
  stepperBtnText: {
    color: '#FFFFFF',
    fontSize: 32,
    fontWeight: 'bold'
  },
  stepperValue: {
    color: '#FFFFFF',
    fontSize: 32,
    fontWeight: '800'
  },
  submitButton: {
    backgroundColor: '#1E56A0',
    borderRadius: 14,
    paddingVertical: 18,
    alignItems: 'center',
    marginBottom: 20,
    shadowColor: '#1E56A0',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 10,
    elevation: 6,
    borderWidth: 1,
    borderColor: '#3B82F6'
  },
  submitBtnText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '900',
    letterSpacing: 1
  },
  historyCard: {
    backgroundColor: '#111827',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#1F2937'
  },
  historyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12
  },
  historyTitle: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '800'
  },
  manualSyncBtn: {
    backgroundColor: '#2563EB',
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 8
  },
  manualSyncText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700'
  },
  lastSyncText: {
    color: '#10B981',
    fontSize: 10,
    fontWeight: '600',
    marginBottom: 8
  },
  noDataText: {
    color: '#6B7280',
    fontSize: 13,
    fontStyle: 'italic',
    textAlign: 'center',
    marginVertical: 12
  },
  historyItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1F2937',
    borderRadius: 10,
    padding: 12,
    marginBottom: 8
  },
  historyItemTitle: {
    color: '#F9FAFB',
    fontSize: 14,
    fontWeight: '700'
  },
  historyItemDetail: {
    color: '#9CA3AF',
    fontSize: 11,
    marginTop: 2
  },
  syncBadge: {
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderRadius: 6
  },
  badgeSynced: {
    backgroundColor: 'rgba(16, 185, 129, 0.15)',
    borderWidth: 0.5,
    borderColor: '#10B981'
  },
  badgePending: {
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderWidth: 0.5,
    borderColor: '#F59E0B'
  },
  badgeText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#FFFFFF'
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
    justifyContent: 'center',
    padding: 24
  },
  modalContent: {
    backgroundColor: '#1E293B',
    borderRadius: 20,
    padding: 24,
    borderWidth: 1,
    borderColor: '#334155'
  },
  modalTitle: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '800',
    marginBottom: 10
  },
  modalDesc: {
    color: '#94A3B8',
    fontSize: 13,
    lineHeight: 18,
    marginBottom: 20
  },
  modalInput: {
    backgroundColor: '#0F172A',
    borderRadius: 10,
    color: '#FFFFFF',
    paddingVertical: 12,
    paddingHorizontal: 16,
    fontSize: 16,
    borderWidth: 1.5,
    borderColor: '#334155',
    marginBottom: 20
  },
  modalBtnRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginHorizontal: -8
  },
  modalBtn: {
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 8,
    marginHorizontal: 8
  },
  modalBtnCancel: {
    backgroundColor: '#475569'
  },
  modalBtnSave: {
    backgroundColor: '#1E56A0'
  },
  modalBtnText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700'
  },
  buttonGroup: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    gap: 10,
    marginTop: 8
  },
  groupBtn: {
    width: '48%',
    paddingVertical: 14,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    marginBottom: 8
  },
  groupBtnSelected: {
    backgroundColor: '#1E56A0',
    borderColor: '#3B82F6',
  },
  groupBtnUnselected: {
    backgroundColor: '#0F172A',
    borderColor: '#334155',
  },
  groupBtnText: {
    fontSize: 13,
    fontWeight: '800',
  },
  groupBtnTextSelected: {
    color: '#FFFFFF'
  },
  groupBtnTextUnselected: {
    color: '#94A3B8'
  }
});
