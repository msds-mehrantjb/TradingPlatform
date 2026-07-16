# Forecast OOS Meta-Features V2

Market-forecast predictions may be used as V2 meta-model features only when
they are generated out of sample.

## Historical Meta-Training

Historical forecast features are generated per outer validation fold:

- train the forecast model only on the fold training period
- score only the later validation period
- store each validation prediction with `trainingWindowStartUtc`,
  `trainingWindowEndUtc`, validation window, fold number, artifact id, and
  feature version
- reject any prediction whose decision timestamp is inside or before the model's
  fitting window

The first feature version is `market_forecast_oos_feature_v1`.

Final full-history forecast artifacts are for deployment/live inference only.
They must not manufacture historical meta-training features.

## Live Inference

Live decisions may use only an approved forecast artifact for the same symbol
whose training-window end timestamp precedes the live decision timestamp.

When no approved pre-decision artifact exists, the forecast feature becomes an
explicit fallback:

`status = missing_approved_forecast_model`

The fallback carries null probabilities and a reason code rather than silently
substituting a full-history or unapproved model.

