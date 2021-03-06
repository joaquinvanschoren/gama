Classification
**************

::

    from sklearn.datasets import load_breast_cancer
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import log_loss, accuracy_score
    from gama import GamaClassifier

    X, y = load_breast_cancer(return_X_y=True)
    X_train, X_test, y_train, y_test = train_test_split(X, y, stratify=y, random_state=0)

    automl = GamaClassifier(max_total_time=180)
    automl.fit(X_train, y_train)

    label_predictions = automl.predict(X_test)
    probability_predictions = automl.predict_proba(X_test)

    print('accuracy:', accuracy_score(y_test, label_predictions))
    print('log loss:', log_loss(y_test, probability_predictions))

Should take 3 minutes to run and give the output below (exact performance might differ)::

    accuracy: 0.951048951048951
    log loss: 0.1111237013184977

By default, GamaClassifier will optimize towards `log loss`.