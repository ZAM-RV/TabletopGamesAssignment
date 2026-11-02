package players.sushiGoDecoupledPB;

import core.AbstractGameState;
import core.actions.AbstractAction;
import core.interfaces.IActionHeuristic;
import games.sushigo.SGGameState;
import games.sushigo.actions.ChooseCard;
import games.sushigo.cards.SGCard;
import players.heuristics.CountingCardsHeuristic;

import java.util.List;

/**
 * Adapts CountingCardsHeuristic (a state heuristic) into an action heuristic for progressive bias.
 *
 * A ChooseCard action only records a choice; the card is not revealed until every player has chosen.
 * So applying the action through the forward model would leave the state unchanged and give every
 * action the same score. Instead we reveal just this player's card on a copy of the state (mirroring
 * SGForwardModel.revealCards) and return the change in the heuristic value.
 *
 * Chopsticks: only the chosen card is scored, the chopsticks returning to hand is ignored.
 */
public class CountingCardsActionHeuristic implements IActionHeuristic {

    private final CountingCardsHeuristic stateHeuristic = new CountingCardsHeuristic();

    @Override
    public double evaluateAction(AbstractAction action, AbstractGameState state, List<AbstractAction> contextActions) {
        ChooseCard cc = (ChooseCard) action;
        double before = stateHeuristic.evaluateState(state, cc.playerId);
        return valueAfterReveal(cc, (SGGameState) state) - before;
    }

    @Override
    public double[] evaluateAllActions(List<AbstractAction> actions, AbstractGameState state) {
        // All actions at a decision belong to the same player, so the baseline only needs computing once
        double[] scores = new double[actions.size()];
        if (actions.isEmpty()) return scores;
        double before = stateHeuristic.evaluateState(state, ((ChooseCard) actions.get(0)).playerId);
        for (int i = 0; i < actions.size(); i++) {
            scores[i] = valueAfterReveal((ChooseCard) actions.get(i), (SGGameState) state) - before;
        }
        return scores;
    }

    private double valueAfterReveal(ChooseCard cc, SGGameState state) {
        SGGameState after = (SGGameState) state.copy();
        SGCard card = after.getPlayerHands().get(cc.playerId).get(cc.cardIdx);

        after.getPlayerHands().get(cc.playerId).remove(card);
        after.getPlayedCards().get(cc.playerId).add(card);
        after.getPlayedCardTypes()[cc.playerId].get(card.type).increment(card.count);
        card.type.onReveal(after, cc.playerId);

        return stateHeuristic.evaluateState(after, cc.playerId);
    }

    @Override
    public String toString() {
        return "CountingCardsActionHeuristic";
    }
}
