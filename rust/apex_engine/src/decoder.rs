use crate::types::Bytes;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecodeError {
    EmptyCalldata,
}

pub fn validate_non_empty_calldata(data: &Bytes) -> Result<(), DecodeError> {
    if data.is_empty() {
        return Err(DecodeError::EmptyCalldata);
    }
    Ok(())
}
