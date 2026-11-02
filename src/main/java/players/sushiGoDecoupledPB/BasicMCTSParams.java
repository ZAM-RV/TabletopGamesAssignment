package players.sushiGoDecoupledPB;

import core.AbstractGameState;
import core.interfaces.IActionHeuristic;
import core.interfaces.IStateHeuristic;
import players.PlayerParameters;

import java.util.Arrays;


public class BasicMCTSParams extends PlayerParameters {

    public double K = Math.sqrt(2);
    public int rolloutLength = 10; // assuming we have a good heuristic
    public int maxTreeDepth = 100; // effectively no limit
    public double epsilon = 1e-6;
    public IStateHeuristic heuristic = AbstractGameState::getHeuristicScore;
    public boolean restrictToCurrentRound = false;
    // Progressive bias: weight W on H(a) / (n_a + 1), where H comes from actionHeuristic. 0 turns it off
    public double progressiveBias = 0.0;
    public IActionHeuristic actionHeuristic = IActionHeuristic.nullReturn;

    public BasicMCTSParams() {
        addTunableParameter("K", Math.sqrt(2), Arrays.asList(0.0, 0.1, 1.0, Math.sqrt(2), 3.0, 10.0));
        addTunableParameter("rolloutLength", 10, Arrays.asList(0, 3, 10, 30, 100));
        addTunableParameter("maxTreeDepth", 100, Arrays.asList(1, 3, 10, 30, 100));
        addTunableParameter("epsilon", 1e-6);
        addTunableParameter("heuristic", (IStateHeuristic) AbstractGameState::getHeuristicScore);
        addTunableParameter("restrictToCurrentRound", false);
        addTunableParameter("progressiveBias", 0.0, Arrays.asList(0.0, 0.3, 1.0, 3.0, 10.0));
        addTunableParameter("actionHeuristic", IActionHeuristic.class, IActionHeuristic.nullReturn);
    }

    @Override
    public void _reset() {
        super._reset();
        K = (double) getParameterValue("K");
        rolloutLength = (int) getParameterValue("rolloutLength");
        maxTreeDepth = (int) getParameterValue("maxTreeDepth");
        epsilon = (double) getParameterValue("epsilon");
        heuristic = (IStateHeuristic) getParameterValue("heuristic");
        restrictToCurrentRound = (boolean) getParameterValue("restrictToCurrentRound");
        progressiveBias = (double) getParameterValue("progressiveBias");
        actionHeuristic = (IActionHeuristic) getParameterValue("actionHeuristic");
    }

    @Override
    protected BasicMCTSParams _copy() {
        // All the copying is done in TunableParameters.copy()
        // Note that any *local* changes of parameters will not be copied
        // unless they have been 'registered' with setParameterValue("name", value)
        return new BasicMCTSParams();
    }

    @Override
    public IStateHeuristic getStateHeuristic() {
        return heuristic;
    }

    @Override
    public BasicMCTSPlayer instantiate() {
        return new BasicMCTSPlayer((BasicMCTSParams) this.copy());
    }

}
