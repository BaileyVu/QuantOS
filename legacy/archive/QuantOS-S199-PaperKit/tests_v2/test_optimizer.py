from s199_v2.optimizer import Candidate, evaluate, fit_platt


def rows():
    result=[]
    for i in range(20):
        up=i%2; token_ids=[f"u{i}",f"d{i}"]
        result.append({"prob_up":.8 if up else .2,"outcome_up":up,"yes_ask":.6 if up else .4,"no_ask":.4 if up else .6,
            "momentum_15s":1 if up else -1,"momentum_30s":1 if up else -1,"token_ids":token_ids,
            "fee_rate_bps_by_token":{token_ids[0]:"700",token_ids[1]:"700"}})
    return result


def test_platt_uses_labels_and_candidate_accounts_for_fees() -> None:
    calibration=fit_platt(rows())
    result=evaluate(rows(),Candidate(calibration,.01,.75,1.02,"aligned"))
    assert calibration["fit_partition"]=="train_only"
    assert result["trades"]==20 and result["pnl"]>0 and result["brier"]<.1
