#!/usr/bin/env python3
"""
Agent de Reinforcement Learning pour Trading Automatique.

Cet agent utilise Q-Learning/DQN pour apprendre les meilleures stratégies
d'entrée et de sortie en fonction du contexte de marché.

Fonctionnalités:
- Q-Learning avec exploration epsilon-greedy
- Deep Q-Network (DQN) pour les espaces d'état complexes
- Intégration avec le système Meta-Learning
- Environnement de trading simulé avec récompenses réalistes
- Stratégies adaptatives selon les conditions de marché
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import warnings

warnings.filterwarnings("ignore")

# Machine Learning & RL
# Neural Networks pour DQN avec fallback robuste
try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers

    TF_AVAILABLE = True
    print("✅ TensorFlow disponible - DQN activé")
except ImportError as e:
    TF_AVAILABLE = False
    print(f"⚠️  TensorFlow non disponible: {e}")
    print("🔄 Fallback vers Q-Learning tabulaire classique")
except Exception as e:
    TF_AVAILABLE = False
    print(f"🔴 Erreur TensorFlow: {e}")
    print("🔄 Fallback vers Q-Learning tabulaire classique")

# Métriques


class TradingEnvironment:
    """Environnement de trading pour l'agent RL"""

    def __init__(self, data, initial_balance=10000, transaction_cost=0.0001):
        """
        Args:
            data: DataFrame avec OHLCV + features
            initial_balance: Capital initial
            transaction_cost: Coût de transaction (en %)
        """
        self.data = data.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.transaction_cost = transaction_cost

        # État du marché
        self.current_step = 0
        self.balance = initial_balance
        self.position = 0  # -1: short, 0: neutre, 1: long
        self.entry_price = 0
        self.total_trades = 0
        self.winning_trades = 0

        # Historique
        self.history = []

        print("🏗️  Environnement trading créé:")
        print(f"  📊 {len(data)} barres de données")
        print(f"  💰 Capital initial: ${initial_balance:,.2f}")
        print(f"  💸 Coût transaction: {transaction_cost*100:.2f}%")

    def reset(self):
        """Réinitialiser l'environnement"""
        self.current_step = 0
        self.balance = self.initial_balance
        self.position = 0
        self.entry_price = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.history = []

        return self.get_state()

    def get_state(self):
        """Obtenir l'état actuel pour l'agent"""
        if self.current_step >= len(self.data):
            return None

        row = self.data.iloc[self.current_step]

        # État de base: OHLC + volume normalisés
        price_features = [
            row.get("open", 0) / row.get("close", 1)
            if row.get("close", 1) != 0
            else 1,
            row.get("high", 0) / row.get("close", 1)
            if row.get("close", 1) != 0
            else 1,
            row.get("low", 0) / row.get("close", 1)
            if row.get("close", 1) != 0
            else 1,
            row.get("volume", 0) / 1000000,  # Normaliser le volume
        ]

        # Features techniques si disponibles
        tech_features = []
        for col in ["sma_5", "sma_20", "rsi_14", "bb_upper", "bb_lower"]:
            if col in row:
                value = row[col]
                if pd.isna(value):
                    tech_features.append(0)
                else:
                    # Normaliser par rapport au prix de clôture
                    if col in ["sma_5", "sma_20", "bb_upper", "bb_lower"]:
                        tech_features.append(
                            value / row.get("close", 1)
                            if row.get("close", 1) != 0
                            else 1
                        )
                    else:  # RSI
                        tech_features.append(value / 100)
            else:
                tech_features.append(0)

        # État de la position
        position_features = [
            self.position / 2 + 0.5,  # Normaliser -1,0,1 -> 0,0.5,1
            (self.balance / self.initial_balance) - 1,  # P&L relatif
            min(
                self.current_step / len(self.data), 1.0
            ),  # Progression temporelle
        ]

        state = np.array(
            price_features + tech_features + position_features,
            dtype=np.float32,
        )

        # Remplacer les NaN et infinis
        state = np.nan_to_num(state, nan=0.0, posinf=1.0, neginf=-1.0)

        return state

    def step(self, action):
        """
        Exécuter une action dans l'environnement

        Args:
            action: 0=hold, 1=buy/long, 2=sell/short, 3=close

        Returns:
            next_state, reward, done, info
        """
        if self.current_step >= len(self.data) - 1:
            return None, 0, True, {"reason": "end_of_data"}

        current_price = self.data.iloc[self.current_step].get("close", 0)
        reward = 0
        info = {"action_taken": action, "price": current_price}

        # Exécuter l'action
        if action == 1 and self.position <= 0:  # Buy/Long
            # Fermer position short d'abord
            if self.position == -1:
                pnl = (self.entry_price - current_price) / self.entry_price
                pnl -= self.transaction_cost  # Coûts
                self.balance *= 1 + pnl
                reward += pnl * 100  # Récompense proportionnelle au P&L

                self.total_trades += 1
                if pnl > 0:
                    self.winning_trades += 1

            # Ouvrir position long
            self.position = 1
            self.entry_price = current_price
            reward -= self.transaction_cost * 10  # Pénalité pour les coûts
            info["new_position"] = "long"

        elif action == 2 and self.position >= 0:  # Sell/Short
            # Fermer position long d'abord
            if self.position == 1:
                pnl = (current_price - self.entry_price) / self.entry_price
                pnl -= self.transaction_cost
                self.balance *= 1 + pnl
                reward += pnl * 100

                self.total_trades += 1
                if pnl > 0:
                    self.winning_trades += 1

            # Ouvrir position short
            self.position = -1
            self.entry_price = current_price
            reward -= self.transaction_cost * 10
            info["new_position"] = "short"

        elif action == 3 and self.position != 0:  # Close position
            if self.position == 1:  # Fermer long
                pnl = (current_price - self.entry_price) / self.entry_price
            else:  # Fermer short
                pnl = (self.entry_price - current_price) / self.entry_price

            pnl -= self.transaction_cost
            self.balance *= 1 + pnl
            reward += pnl * 100

            self.total_trades += 1
            if pnl > 0:
                self.winning_trades += 1

            self.position = 0
            self.entry_price = 0
            info["closed_position"] = pnl

        # Récompense pour tenir une position gagnante
        if self.position != 0:
            if self.position == 1:  # Long
                unrealized_pnl = (
                    current_price - self.entry_price
                ) / self.entry_price
            else:  # Short
                unrealized_pnl = (
                    self.entry_price - current_price
                ) / self.entry_price

            reward += (
                unrealized_pnl * 10
            )  # Récompense plus faible pour P&L non réalisé

        # Pénalité pour drawdown excessif
        current_equity = self.balance
        if self.position != 0:
            if self.position == 1:
                unrealized = (
                    current_price - self.entry_price
                ) / self.entry_price
            else:
                unrealized = (
                    self.entry_price - current_price
                ) / self.entry_price
            current_equity *= 1 + unrealized - self.transaction_cost

        drawdown = 1 - (current_equity / self.initial_balance)
        if drawdown > 0.1:  # Pénalité si drawdown > 10%
            reward -= drawdown * 50

        # Avancer au step suivant
        self.current_step += 1

        # Sauvegarder l'historique
        self.history.append(
            {
                "step": self.current_step - 1,
                "action": action,
                "price": current_price,
                "position": self.position,
                "balance": self.balance,
                "reward": reward,
            }
        )

        # État suivant
        next_state = self.get_state()
        done = (self.current_step >= len(self.data) - 1) or (
            current_equity < self.initial_balance * 0.5
        )

        return next_state, reward, done, info

    def get_performance_metrics(self):
        """Calculer les métriques de performance"""
        if not self.history:
            return {}

        final_balance = self.balance
        if self.position != 0 and self.current_step < len(self.data):
            # Inclure P&L non réalisé
            current_price = self.data.iloc[
                min(self.current_step, len(self.data) - 1)
            ].get("close", self.entry_price)
            if self.position == 1:
                unrealized = (
                    current_price - self.entry_price
                ) / self.entry_price
            else:
                unrealized = (
                    self.entry_price - current_price
                ) / self.entry_price
            final_balance *= 1 + unrealized - self.transaction_cost

        total_return = (final_balance / self.initial_balance) - 1
        win_rate = (
            self.winning_trades / self.total_trades
            if self.total_trades > 0
            else 0
        )

        # Calculer le Sharpe ratio approximatif
        returns = []
        prev_balance = self.initial_balance
        for entry in self.history[::10]:  # Échantillonner tous les 10 steps
            returns.append((entry["balance"] / prev_balance) - 1)
            prev_balance = entry["balance"]

        sharpe = 0
        if len(returns) > 1:
            returns_mean = np.mean(returns)
            returns_std = np.std(returns)
            if returns_std > 0:
                sharpe = returns_mean / returns_std * np.sqrt(252)  # Annualisé

        return {
            "total_return": total_return,
            "final_balance": final_balance,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate": win_rate,
            "sharpe_ratio": sharpe,
        }


class QLearningAgent:
    """Agent Q-Learning pour trading"""

    def __init__(
        self,
        state_size,
        action_size=4,
        learning_rate=0.1,
        discount_factor=0.95,
        exploration_rate=1.0,
        exploration_decay=0.995,
    ):
        """
        Args:
            state_size: Taille de l'espace d'état
            action_size: Nombre d'actions (4: hold, buy, sell, close)
            learning_rate: Taux d'apprentissage
            discount_factor: Facteur d'actualisation
            exploration_rate: Taux d'exploration initial
            exploration_decay: Décroissance de l'exploration
        """
        self.state_size = state_size
        self.action_size = action_size
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.exploration_rate = exploration_rate
        self.exploration_decay = exploration_decay
        self.exploration_min = 0.01

        # Q-table discrétisée (pour simplifier)
        self.state_bins = 10  # Nombre de bins par dimension d'état
        self.q_table_shape = tuple(
            [self.state_bins] * state_size + [action_size]
        )
        self.q_table = np.random.uniform(
            low=-0.01, high=0.01, size=self.q_table_shape
        )

        print("🧠 QLearningAgent créé:")
        print(f"  🎯 Actions: {action_size}")
        print(f"  📊 État: {state_size} dimensions")
        print(f"  🎲 Exploration: {exploration_rate:.2f}")

    def discretize_state(self, state):
        """Discrétiser l'état continu pour la Q-table"""
        # Normaliser l'état entre 0 et 1
        state_normalized = np.clip(state, -2, 2) / 4 + 0.5

        # Discrétiser
        discrete_state = []
        for i, value in enumerate(state_normalized):
            bin_index = min(int(value * self.state_bins), self.state_bins - 1)
            discrete_state.append(bin_index)

        return tuple(discrete_state)

    def get_action(self, state):
        """Sélectionner une action (exploration vs exploitation)"""
        if np.random.random() < self.exploration_rate:
            # Exploration: action aléatoire
            return np.random.randint(self.action_size)
        else:
            # Exploitation: meilleure action selon Q-table
            discrete_state = self.discretize_state(state)
            q_values = self.q_table[discrete_state]
            return np.argmax(q_values)

    def learn(self, state, action, reward, next_state, done):
        """Mise à jour Q-Learning"""
        discrete_state = self.discretize_state(state)

        # Q-value actuel
        current_q = self.q_table[discrete_state + (action,)]

        if done or next_state is None:
            # Pas d'état futur
            target_q = reward
        else:
            # Meilleure Q-value de l'état suivant
            discrete_next_state = self.discretize_state(next_state)
            max_next_q = np.max(self.q_table[discrete_next_state])
            target_q = reward + self.discount_factor * max_next_q

        # Mise à jour Q-table
        self.q_table[discrete_state + (action,)] += self.learning_rate * (
            target_q - current_q
        )

        # Décroissance de l'exploration
        if self.exploration_rate > self.exploration_min:
            self.exploration_rate *= self.exploration_decay


class DQNAgent:
    """Agent Deep Q-Network (si TensorFlow disponible)"""

    def __init__(
        self,
        state_size,
        action_size=4,
        learning_rate=0.001,
        discount_factor=0.95,
        exploration_rate=1.0,
        exploration_decay=0.995,
    ):
        if not TF_AVAILABLE:
            raise ImportError("TensorFlow requis pour DQNAgent")

        self.state_size = state_size
        self.action_size = action_size
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.exploration_rate = exploration_rate
        self.exploration_decay = exploration_decay
        self.exploration_min = 0.01

        # Replay buffer
        self.memory = []
        self.memory_size = 10000
        self.batch_size = 32

        # Réseau de neurones
        self.model = self._build_model()
        self.target_model = self._build_model()
        self.update_target_model()

        print("🚀 DQNAgent créé:")
        print(f"  🧠 Réseau: {state_size} -> 128 -> 64 -> {action_size}")
        print(f"  💾 Mémoire: {self.memory_size} expériences")

    def _build_model(self):
        """Construire le réseau de neurones DQN"""
        model = keras.Sequential(
            [
                layers.Dense(
                    128, activation="relu", input_dim=self.state_size
                ),
                layers.Dropout(0.2),
                layers.Dense(64, activation="relu"),
                layers.Dropout(0.2),
                layers.Dense(32, activation="relu"),
                layers.Dense(self.action_size, activation="linear"),
            ]
        )

        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=self.learning_rate),
            loss="mse",
        )

        return model

    def update_target_model(self):
        """Copier les poids du modèle principal vers le modèle cible"""
        self.target_model.set_weights(self.model.get_weights())

    def remember(self, state, action, reward, next_state, done):
        """Stocker l'expérience dans la mémoire"""
        self.memory.append((state, action, reward, next_state, done))
        if len(self.memory) > self.memory_size:
            self.memory.pop(0)

    def get_action(self, state):
        """Sélectionner une action avec epsilon-greedy"""
        if np.random.random() < self.exploration_rate:
            return np.random.randint(self.action_size)

        state_tensor = tf.expand_dims(state, 0)
        q_values = self.model(state_tensor)
        return np.argmax(q_values.numpy()[0])

    def replay_training(self):
        """Entraînement par replay d'expérience"""
        if len(self.memory) < self.batch_size:
            return

        # Échantillonner un batch aléatoire
        batch_indices = np.random.choice(
            len(self.memory), self.batch_size, replace=False
        )
        batch = [self.memory[i] for i in batch_indices]

        states = np.array([e[0] for e in batch])
        next_states = np.array([e[3] for e in batch if e[3] is not None])

        # Prédictions actuelles
        current_q_values = self.model.predict(states, verbose=0)

        # Prédictions futures (modèle cible)
        future_q_values = (
            self.target_model.predict(next_states, verbose=0)
            if len(next_states) > 0
            else []
        )

        # Mise à jour des cibles
        for i, (state, action, reward, next_state, done) in enumerate(batch):
            if done or next_state is None:
                target_q = reward
            else:
                target_q = reward + self.discount_factor * np.max(
                    future_q_values[i]
                )

            current_q_values[i][action] = target_q

        # Entraînement
        self.model.fit(states, current_q_values, epochs=1, verbose=0)

        # Décroissance exploration
        if self.exploration_rate > self.exploration_min:
            self.exploration_rate *= self.exploration_decay


class ReinforcementLearningTradingSystem:
    """Système de trading avec Reinforcement Learning"""

    def __init__(self, use_dqn=False, meta_learning_integration=True):
        """
        Args:
            use_dqn: Utiliser DQN si TensorFlow disponible
            meta_learning_integration: Intégrer avec le système Meta-Learning
        """
        self.use_dqn = use_dqn and TF_AVAILABLE
        self.meta_learning_integration = meta_learning_integration

        self.environment = None
        self.agent = None
        self.training_history = []

        print("🎯 Système RL Trading initialisé:")
        print(f"  🤖 Agent: {'DQN' if self.use_dqn else 'Q-Learning'}")
        ml_status = "✅" if meta_learning_integration else "❌"
        print(f"  🧠 Meta-Learning: {ml_status}")

    def prepare_data(self, df):
        """Préparer les données pour l'environnement RL"""
        # Charger features avancées si disponibles
        try:
            enhanced_df = pd.read_csv("data/features_enhanced.csv")
            if "Unnamed: 0" in enhanced_df.columns:
                enhanced_df = enhanced_df.set_index("Unnamed: 0")
            print("✅ Features avancées chargées pour RL")
            return enhanced_df
        except Exception:
            print("⚠️  Utilisation des features de base pour RL")
            return df

    def train_agent(self, df, episodes=100, verbose=True):
        """Entraîner l'agent RL"""
        print(f"🎓 ENTRAÎNEMENT AGENT RL - {episodes} épisodes")
        print("=" * 50)

        # Préparer les données
        data = self.prepare_data(df)

        # Créer l'environnement
        self.environment = TradingEnvironment(data)
        state_size = len(self.environment.get_state())

        # Créer l'agent
        if self.use_dqn:
            self.agent = DQNAgent(state_size)
        else:
            self.agent = QLearningAgent(state_size)

        # Entraînement
        episode_rewards = []
        episode_returns = []

        for episode in range(episodes):
            state = self.environment.reset()
            total_reward = 0
            steps = 0

            while True:
                # Choisir action
                action = self.agent.get_action(state)

                # Exécuter action
                next_state, reward, done, info = self.environment.step(action)

                # Apprendre
                if self.use_dqn:
                    self.agent.remember(
                        state, action, reward, next_state, done
                    )
                    if len(self.agent.memory) > self.agent.batch_size:
                        self.agent.replay_training()
                else:
                    self.agent.learn(state, action, reward, next_state, done)

                total_reward += reward
                steps += 1

                if done:
                    break

                state = next_state

            # Métriques de l'épisode
            perf_metrics = self.environment.get_performance_metrics()
            episode_rewards.append(total_reward)
            episode_returns.append(perf_metrics.get("total_return", 0))

            # Mise à jour du modèle cible (DQN)
            if self.use_dqn and episode % 10 == 0:
                self.agent.update_target_model()

            # Affichage périodique
            if verbose and (episode + 1) % 20 == 0:
                recent_returns = episode_returns[-20:]
                avg_return = np.mean(recent_returns) * 100

                print(
                    f"  Épisode {episode+1:3d}: Reward={total_reward:6.1f}, "
                    f"Return={perf_metrics.get('total_return', 0)*100:5.1f}%, "
                    f"Avg20={avg_return:5.1f}%, "
                    f"WinRate={perf_metrics.get('win_rate', 0)*100:4.1f}%, "
                    f"Trades={perf_metrics.get('total_trades', 0):2d}"
                )

        # Sauvegarder historique
        self.training_history = {
            "episode_rewards": episode_rewards,
            "episode_returns": episode_returns,
            "final_performance": perf_metrics,
        }

        print("\n✅ Entraînement terminé:")
        total_ret = perf_metrics.get("total_return", 0) * 100
        print(f"  🎯 Dernier return: {total_ret:.2f}%")
        print(f"  🏆 Win rate: {perf_metrics.get('win_rate', 0)*100:.1f}%")
        print(f"  📊 Trades: {perf_metrics.get('total_trades', 0)}")

        return perf_metrics

    def test_agent(self, df, verbose=True):
        """Tester l'agent entraîné"""
        if not self.agent or not self.environment:
            raise ValueError("Agent non entraîné")

        print("\n🧪 TEST AGENT RL")
        print("=" * 30)

        # Reset pour test
        state = self.environment.reset()
        total_reward = 0
        actions_taken = []

        # Test sans exploration (exploitation pure)
        original_exploration = self.agent.exploration_rate
        self.agent.exploration_rate = 0

        while True:
            action = self.agent.get_action(state)
            next_state, reward, done, info = self.environment.step(action)

            total_reward += reward
            actions_taken.append(action)

            if done:
                break

            state = next_state

        # Restaurer exploration
        self.agent.exploration_rate = original_exploration

        # Résultats
        perf_metrics = self.environment.get_performance_metrics()

        if verbose:
            total_ret = perf_metrics.get("total_return", 0) * 100
            print(f"  💰 Return: {total_ret:.2f}%")
            print(f"  🎯 Win Rate: {perf_metrics.get('win_rate', 0)*100:.1f}%")
            print(f"  📊 Trades: {perf_metrics.get('total_trades', 0)}")
            print(f"  📈 Sharpe: {perf_metrics.get('sharpe_ratio', 0):.2f}")

            # Distribution des actions
            action_names = ["Hold", "Buy", "Sell", "Close"]
            action_counts = np.bincount(actions_taken, minlength=4)
            for i, (name, count) in enumerate(
                zip(action_names, action_counts)
            ):
                pct = count / len(actions_taken) * 100 if actions_taken else 0
                print(f"  {name}: {count} ({pct:.1f}%)")

        return perf_metrics

    def save_agent(self, filepath="artifacts/rl_agent"):
        """Sauvegarder l'agent entraîné"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        if self.use_dqn and self.agent:
            # Sauvegarder le modèle DQN
            self.agent.model.save(f"{filepath}_dqn_model.h5")

        # Sauvegarder les métadonnées
        metadata = {
            "agent_type": "DQN" if self.use_dqn else "Q-Learning",
            "training_history": self.training_history,
            "timestamp": datetime.now().isoformat(),
        }

        with open(f"{filepath}_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        print(f"💾 Agent sauvegardé: {filepath}")


def main():
    """Test du système RL Trading"""
    print("🎯 TEST SYSTÈME REINFORCEMENT LEARNING")
    print("=" * 45)

    try:
        # Charger les données
        df = pd.read_csv("data/features_sample.csv")
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            df.index = pd.to_datetime(df.index)

        # Créer le système RL
        rl_system = ReinforcementLearningTradingSystem(
            use_dqn=TF_AVAILABLE, meta_learning_integration=True
        )

        # Entraînement
        train_perf = rl_system.train_agent(df, episodes=50)

        # Test
        test_perf = rl_system.test_agent(df)

        # Sauvegarder
        rl_system.save_agent()

        print("\n🎊 RÉSULTATS FINAUX:")
        train_ret = train_perf.get("total_return", 0) * 100
        print(f"  🎓 Train Return: {train_ret:.2f}%")
        print(f"  🧪 Test Return: {test_perf.get('total_return', 0)*100:.2f}%")
        print(f"  📊 Test Trades: {test_perf.get('total_trades', 0)}")
        print(f"  🏆 Test Win Rate: {test_perf.get('win_rate', 0)*100:.1f}%")

        print("\n✅ Système RL complété avec succès")

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
