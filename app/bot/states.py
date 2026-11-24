from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    choosing_house = State()
    entering_apartment = State()


class FeedbackStates(StatesGroup):
    entering_message = State()
